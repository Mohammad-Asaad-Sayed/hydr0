import json
import time
import inspect
from datetime import datetime
from typing import List, Optional, Any

from hydra_db import HydraDB

from .models import MemoryFact


def obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


# ---------------------------------------------------------------------------
# Query wrapper: probe SDK signature ONCE, filter kwargs up front.
# ---------------------------------------------------------------------------
_QUERY_SIG_PARAMS = None


def hydra_query(client_obj: HydraDB, **kwargs):
    """
    Call client.query. We already removed the unsupported temporal kwargs 
    in memory.py, so this should just work. If the SDK rejects something 
    else, we catch it and drop only the offending keys.
    """
    try:
        return client_obj.query(**kwargs)
    except TypeError as e:
        try:
            sig_params = set(inspect.signature(client_obj.query).parameters.keys())
        except (TypeError, ValueError):
            raise  # can't introspect, surface original error
        unsupported = set(kwargs) - sig_params
        if not unsupported:
            raise  # TypeError wasn't about a kwarg we can drop
        print(f"  [hydra_query] SDK rejected {sorted(unsupported)}; dropping and retrying.")
        trimmed = {k: v for k, v in kwargs.items() if k not in unsupported}
        return client_obj.query(**trimmed)


# ---------------------------------------------------------------------------
# Visible chunk-drop accounting (no silent failures)
# ---------------------------------------------------------------------------
_CHUNK_DROP_COUNT = 0


def _record_chunk_drop(reason: str, chunk_id: Any) -> None:
    global _CHUNK_DROP_COUNT
    _CHUNK_DROP_COUNT += 1
    print(f"  [hydra_chunk_to_fact] dropped chunk {chunk_id!r}: {reason} "
          f"(total dropped: {_CHUNK_DROP_COUNT})")


def reset_chunk_drop_count() -> None:
    global _CHUNK_DROP_COUNT
    _CHUNK_DROP_COUNT = 0


def get_chunk_drop_count() -> int:
    return _CHUNK_DROP_COUNT


def hydra_chunk_to_fact(chunk: Any) -> Optional[MemoryFact]:
    """Convert a retrieved HydraDB chunk into a MemoryFact. Drops are logged, never silent."""
    metadata = obj_get(chunk, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}

    additional = obj_get(chunk, "additional_metadata", {}) or {}
    if isinstance(additional, dict):
        metadata = {**metadata, **additional}

    fact_id = metadata.get("fact_id") or obj_get(chunk, "id") or obj_get(chunk, "chunk_uuid")
    fields = {
        "fact_id": fact_id,
        "subject": metadata.get("subject"),
        "predicate": metadata.get("predicate"),
        "object_value": metadata.get("object_value"),
        "timestamp": metadata.get("timestamp"),
        "session_id": metadata.get("session_id"),
    }
    missing = [k for k, v in fields.items() if not v]
    if missing:
        _record_chunk_drop(f"missing metadata fields {missing}", fact_id)
        return None

    try:
        timestamp = fields["timestamp"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        supersedes = metadata.get("supersedes_fact_id") or None
        if supersedes == "":
            supersedes = None
        return MemoryFact(
            fact_id=str(fields["fact_id"]),
            subject=fields["subject"],
            predicate=fields["predicate"],
            object_value=fields["object_value"],
            timestamp=timestamp,
            session_id=fields["session_id"],
            fact_type=metadata.get("fact_type", "volatile"),
            supersedes_fact_id=supersedes,
            source_text=obj_get(chunk, "chunk_content") or obj_get(chunk, "content"),
            confidence=float(metadata.get("confidence", 1.0)),
        )
    except Exception as e:
        _record_chunk_drop(f"parse error: {e}", fact_id)
        return None


# ---------------------------------------------------------------------------
# Database lifecycle + BYOG ingestion
# ---------------------------------------------------------------------------
def ensure_database_ready(client: HydraDB, database: str, poll_seconds: int = 3, timeout_seconds: int = 180) -> None:
    try:
        client.databases.create(database=database)
    except Exception as exc:
        print(f"Database create note: {exc}")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        infra = client.databases.status(database=database).data.infra
        if infra.ready_for_ingestion:
            print(f"Database ready: {database}")
            return
        time.sleep(poll_seconds)
    raise TimeoutError(f"Database '{database}' was not ready within {timeout_seconds}s.")


def reset_database(client: HydraDB, database: str) -> None:
    """Best-effort wipe + recreate so repeated runs don't accumulate duplicates."""
    try:
        client.databases.delete(database=database)
        print(f"Deleted existing database: {database}")
    except Exception as exc:
        print(f"Delete skipped ({exc}). Use a fresh DATABASE name if this SDK lacks delete.")
    ensure_database_ready(client, database)


def ingest_facts_with_graph(client: HydraDB, facts_to_ingest: List[MemoryFact], database: str) -> List[str]:
    """Ingest memories with a BYOG payload declaring SUPERSEDES edges."""
    memories = [f.to_memory_item() for f in facts_to_ingest]
    graph_payload = {f.fact_id: f.to_graph_entities_and_relations() for f in facts_to_ingest}

    response = client.context.ingest(
        type="memory",
        database=database,
        memories=json.dumps(memories),
        graph_payload=json.dumps(graph_payload),
    )
    ids = [r.id for r in response.data.results]
    print(f"Ingested {len(ids)} memories with BYOG graph.")
    return ids


def wait_for_indexing(client: HydraDB, ids: List[str], database: str, poll_seconds: int = 2, timeout_seconds: int = 180) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        statuses = client.context.status(database=database, ids=ids).data.statuses
        if any(s.indexing_status == "errored" for s in statuses):
            errors = [getattr(s, "error_message", "unknown") for s in statuses if s.indexing_status == "errored"]
            raise RuntimeError(f"HydraDB indexing error(s): {errors}")
        if all(s.indexing_status == "completed" for s in statuses):
            print("Indexing completed (graph ready).")
            return
        time.sleep(poll_seconds)
    raise TimeoutError("HydraDB indexing did not complete before timeout.")