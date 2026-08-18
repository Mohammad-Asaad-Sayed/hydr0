import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from .extraction import extract_facts_from_sessions, link_supersession
from hydra_db import HydraDB

from .models import MemoryState
from .extraction import extract_facts_from_sessions
from .resolver import (
    resolve_revision_state,
    resolve_multi_hop,
    extract_target_from_query,
    extract_multi_hop_target,
)
from . import graph as g


class HydraMemory:
    """Graph-native temporal memory for AI agents. Drop-in alternative to vector-only memory."""

    def __init__(self, database: str = "hydramem_default", api_key: Optional[str] = None):
        self.database = database
        self.api_key = api_key or os.getenv("HYDRA_DB_API_KEY")
        if not self.api_key:
            raise ValueError("HYDRA_DB_API_KEY must be set (env var or api_key arg).")
        self.client = HydraDB(token=self.api_key)
        g.ensure_database_ready(self.client, self.database)

    # ------------------------------------------------------------------ write
    def add(
        self,
        messages: List[Dict[str, str]],
        user_id: str = "user",
        session_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> List[str]:
        """Extract facts from ONE session and ingest with BYOG graph.

        Note: supersession linking happens only within this call. To build
        revision chains ACROSS sessions, use add_sessions() with the full history.
        """
        session_id = session_id or f"sess_{int(time.time())}"
        timestamp = timestamp or datetime.now(timezone.utc)

        session_payload = [{
            "session_id": session_id,
            "timestamp": timestamp,
            "turns": [{"role": m["role"], "content": m["content"]} for m in messages],
        }]

        facts = extract_facts_from_sessions(
            session_payload, default_subject=user_id, fact_id_prefix=f"auto_{session_id}"
        )
        if not facts:
            return []

        ids = g.ingest_facts_with_graph(self.client, facts, self.database)
        g.wait_for_indexing(self.client, ids, self.database)
        return ids

    def add_sessions(self, sessions: List[Dict[str, Any]]) -> List[str]:
        """Batch-ingest multiple sessions WITH cross-session supersession linking.

        sessions: list of dicts:
            {"session_id": str, "user_id": str, "timestamp": datetime,
             "messages": [{"role": "user", "content": "..."}]}

        Facts are grouped per user so revision chains (SUPERSEDES edges) are
        built across the full history before ingestion.
        """
        by_user: Dict[str, List[Dict]] = defaultdict(list)
        for s in sessions:
            by_user[s.get("user_id", "user")].append({
                "session_id": s["session_id"],
                "timestamp": s["timestamp"],
                "turns": [{"role": m["role"], "content": m["content"]} for m in s["messages"]],
            })

        all_ids: List[str] = []
        for user_id, sess_payload in by_user.items():
            facts = extract_facts_from_sessions(
                sess_payload, default_subject=user_id, fact_id_prefix=f"auto_{user_id}"
            )
            if not facts:
                continue
            ids = g.ingest_facts_with_graph(self.client, facts, self.database)
            g.wait_for_indexing(self.client, ids, self.database)
            all_ids.extend(ids)
        return all_ids

    def add_facts(
        self,
        facts: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        auto_link_supersession: bool = True,
    ) -> List[str]:
        """Ingest pre-typed facts directly, skipping text extraction entirely.

        Each fact dict:
            required: predicate, object_value
            optional: subject (falls back to user_id), timestamp, fact_type,
                      supersedes_fact_id, source_text, confidence, session_id, fact_id

        Set auto_link_supersession=False if you manage SUPERSEDES edges yourself.
        Facts with an explicit supersedes_fact_id are never auto-relinked.
        """
        from datetime import datetime, timezone

        memory_facts: List[MemoryFact] = []
        for i, raw in enumerate(facts):
            subject = raw.get("subject") or user_id or "user"
            predicate = raw["predicate"]
            timestamp = raw.get("timestamp") or datetime.now(timezone.utc)
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

            memory_facts.append(MemoryFact(
                fact_id=raw.get("fact_id") or f"custom_{subject}_{predicate}_{i:03d}",
                subject=subject,
                predicate=predicate,
                object_value=str(raw["object_value"]),
                timestamp=timestamp,
                session_id=raw.get("session_id", "custom"),
                fact_type=raw.get("fact_type", "volatile"),
                supersedes_fact_id=raw.get("supersedes_fact_id"),
                source_text=raw.get("source_text"),
                confidence=float(raw.get("confidence", 1.0)),
            ))

        # Auto-link only facts that didn't bring their own supersedes edge
        if auto_link_supersession:
            manual = [f for f in memory_facts if f.supersedes_fact_id]
            auto = [f for f in memory_facts if not f.supersedes_fact_id]
            memory_facts = link_supersession(auto) + manual

        ids = g.ingest_facts_with_graph(self.client, memory_facts, self.database)
        g.wait_for_indexing(self.client, ids, self.database)
        return ids

    # ------------------------------------------------------------------- read
    def search(self, query: str, user_id: str = "user", top_k: int = 30) -> Dict[str, Any]:
        """Query the memory graph for a specific user. Deterministic verdict, not a guess."""
        multi_hop_target = extract_multi_hop_target(query)

        g.reset_chunk_drop_count()
        result = g.hydra_query(
            self.client,
            database=self.database,
            query=query,
            type="memory",
            query_by="hybrid",
            mode="thinking",
            max_results=top_k,
            graph_context=True,
        )
        chunks = g.obj_get(result.data, "chunks", []) or []
        graph_ctx = g.obj_get(result.data, "graph_context", {}) or {}

        candidate_facts = []
        for ch in chunks:
            fact = g.hydra_chunk_to_fact(ch)
            if fact:
                candidate_facts.append(fact)
        if g.get_chunk_drop_count():
            print(f"  [search] WARNING: {g.get_chunk_drop_count()} chunk(s) dropped -- see log above.")

        if multi_hop_target:
            _subject, relation_predicate, target_predicate = multi_hop_target
            resolution = resolve_multi_hop(candidate_facts, user_id, relation_predicate, target_predicate)
            target_label = f"{user_id}::{relation_predicate}->{target_predicate}"
            hops_payload = [
                {
                    "state": h["state"],
                    "answer": h["fact"].object_value if h.get("fact") else None,
                    "reason": h["reason"],
                }
                for h in resolution["hops"]
            ]
        else:
            _subject, predicate = extract_target_from_query(query)
            resolution = resolve_revision_state(candidate_facts, user_id, predicate)
            target_label = f"{user_id}::{predicate}"
            hops_payload = None

        answer = None
        if resolution["state"] == MemoryState.ANSWERABLE.value and resolution["fact"]:
            answer = resolution["fact"].object_value

        return {
            "query": query,
            "target": target_label,
            "multi_hop": bool(multi_hop_target),
            "related_entity": resolution.get("related_entity"),
            "state": resolution["state"],
            "answer": answer,
            "reason": resolution["reason"],
            "hops": hops_payload,
            "revision_chain": [
                {"fact_id": f.fact_id, "value": f.object_value, "ts": f.timestamp.isoformat()}
                for f in resolution.get("chain", [])
            ] if not multi_hop_target else [],
            "retrieved_candidates": len(candidate_facts),
            "graph_context": {
                "query_paths": g.obj_get(graph_ctx, "query_paths", []),
                "chunk_relations": g.obj_get(graph_ctx, "chunk_relations", []),
            },
        }