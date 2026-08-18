import os
from datetime import datetime, timezone

from hydramem import HydraMemory

print("🚀 Initializing HydraMemory (Graph-Native Agent Memory)...")
mem = HydraMemory(database="hack_hydra_demo_2026_v2")   # fresh DB — old one is polluted

print("\n🗣️ Ingesting 3 sessions across time (Aug 1 → Aug 5 → Aug 10)...")
mem.add_sessions([
    {
        "session_id": "sess_1", "user_id": "alice",
        "timestamp": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "messages": [{"role": "user", "content": "Hey, I'm based in New York these days."}],
    },
    {
        "session_id": "sess_2", "user_id": "alice",
        "timestamp": datetime(2026, 8, 5, tzinfo=timezone.utc),
        "messages": [{"role": "user", "content": "I really prefer dark mode for all my apps."}],
    },
    {
        "session_id": "sess_3", "user_id": "alice",
        "timestamp": datetime(2026, 8, 10, tzinfo=timezone.utc),
        "messages": [{"role": "user", "content": "Quick update: I relocated to London for work."}],
    },
])

print("\n" + "=" * 50)
print("🔍 Query 1: 'Where does the user live?'")
r1 = mem.search("Where does the user live?", user_id="alice")
print(f"State : {r1['state']}")
print(f"Answer: {r1['answer']}  (graph knows London supersedes New York)")
print(f"Chain : {[s['value'] for s in r1['revision_chain']]}")
print(f"Chunks retrieved: {r1['retrieved_candidates']}")

print("\n🔍 Query 2: 'What is the user's favorite pet?'")
r2 = mem.search("What is the user's favorite pet?", user_id="alice")
print(f"State : {r2['state']}  (correct abstention — vectors hallucinate here)")
print(f"Chunks retrieved: {r2['retrieved_candidates']}")

print("\n🔍 Query 3: 'What does the user prefer?'")
r3 = mem.search("What does the user prefer?", user_id="alice")
print(f"State : {r3['state']}")
print(f"Answer: {r3['answer']}")
print(f"Chunks retrieved: {r3['retrieved_candidates']}")