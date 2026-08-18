import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

import pandas as pd

from .models import MemoryFact, MemoryState
from .memory import HydraMemory
from .resolver import resolve_revision_state, extract_target_from_query


def benchmark_read_latency(
    mem: HydraMemory,
    queries: Optional[List[str]] = None,
    repeats: int = 2,
) -> pd.DataFrame:
    """Measure end-to-end live query latency on the multi-hop-aware path."""
    queries = queries or [
        "Where does the user live?",
        "What does the user prefer?",
        "What is the user's favorite pet?",
        "Where does my manager live?",
    ]
    rows = []
    for query in queries:
        samples, result = [], None
        for _ in range(max(1, repeats)):
            t0 = time.perf_counter()
            result = mem.search(query)
            samples.append((time.perf_counter() - t0) * 1000.0)
        samples.sort()
        rows.append({
            "query": query,
            "state": result["state"],
            "answer": result["answer"],
            "retrieved_candidates": result["retrieved_candidates"],
            "p50_ms": samples[len(samples) // 2],
            "min_ms": samples[0],
            "max_ms": samples[-1],
        })
    return pd.DataFrame(rows)


def benchmark_write_cost(mem: HydraMemory, n_facts: int = 5) -> Dict[str, float]:
    """Measure ingest + graph-indexing latency per fact (read AND write cost)."""
    from .graph import ingest_facts_with_graph, wait_for_indexing

    test_facts = [
        MemoryFact(
            fact_id=f"write_bench_{i}",
            subject="user",
            predicate="benchmarked_in",
            object_value=f"City_{i}",
            timestamp=datetime.now(timezone.utc),
            session_id=f"bench_{i:03d}",
            source_text=f"Benchmarking write cost in City_{i}.",
        )
        for i in range(n_facts)
    ]

    t0 = time.perf_counter()
    ids = ingest_facts_with_graph(mem.client, test_facts, mem.database)
    write_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    wait_for_indexing(mem.client, ids, mem.database, timeout_seconds=60)
    index_ms = (time.perf_counter() - t0) * 1000.0

    print(f"Write cost ({n_facts} facts):")
    print(f"  Ingest API call : {write_ms:.1f} ms ({write_ms / n_facts:.1f} ms/fact)")
    print(f"  Graph indexing  : {index_ms:.1f} ms ({index_ms / n_facts:.1f} ms/fact)")
    print(f"  Total to ready  : {write_ms + index_ms:.1f} ms")
    return {"ingest_total_ms": write_ms, "indexing_total_ms": index_ms}