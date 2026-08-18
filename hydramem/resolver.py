import re
from typing import List, Dict, Any, Optional, Tuple

from .models import MemoryFact, MemoryState


# ---------------------------------------------------------------------------
# Deterministic revision-chain resolution
# ---------------------------------------------------------------------------
def resolve_revision_state(
    candidate_facts: List[MemoryFact],
    subject: str,
    predicate: str,
) -> Dict[str, Any]:
    """Resolve only facts matching (subject, predicate). Never trusts ranking to decide truth."""
    relevant = [
        f for f in candidate_facts if f.subject == subject and f.predicate == predicate
    ]

    if not relevant:
        return {
            "state": MemoryState.NO_EVIDENCE.value,
            "fact": None,
            "chain": [],
            "reason": "No fact exists for the requested subject/property.",
        }

    by_id = {f.fact_id: f for f in relevant}
    superseded_ids = {f.supersedes_fact_id for f in relevant if f.supersedes_fact_id}
    current = [f for f in relevant if f.fact_id not in superseded_ids]

    if not current:
        return {
            "state": MemoryState.CONFLICTING.value,
            "fact": None,
            "chain": sorted(relevant, key=lambda f: f.timestamp),
            "reason": "Revision graph has no terminal fact.",
        }

    newest_ts = max(f.timestamp for f in current)
    newest = [f for f in current if f.timestamp == newest_ts]

    if len(newest) > 1:
        distinct_values = {f.object_value for f in newest}
        if len(distinct_values) > 1:
            return {
                "state": MemoryState.CONFLICTING.value,
                "fact": None,
                "chain": sorted(relevant, key=lambda f: f.timestamp),
                "reason": "Multiple current contradictory values exist at the same timestamp.",
            }

    winner = sorted(current, key=lambda f: (f.timestamp, f.confidence), reverse=True)[0]

    # Reconstruct revision chain backwards
    chain, cursor, seen = [], winner, set()
    while cursor and cursor.fact_id not in seen:
        seen.add(cursor.fact_id)
        chain.append(cursor)
        if not cursor.supersedes_fact_id:
            break
        cursor = by_id.get(cursor.supersedes_fact_id)

    return {
        "state": MemoryState.ANSWERABLE.value,
        "fact": winner,
        "chain": list(reversed(chain)),
        "reason": "Resolved from the terminal fact in the revision chain.",
    }


def resolve_multi_hop(
    candidate_facts: List[MemoryFact],
    subject: str,
    relation_predicate: str,
    target_predicate: str,
) -> Dict[str, Any]:
    """Two-hop resolution: (subject, relation) -> entity, then (entity, target)."""
    hop1 = resolve_revision_state(candidate_facts, subject, relation_predicate)
    if hop1["state"] != MemoryState.ANSWERABLE.value:
        return {
            "state": hop1["state"],
            "fact": None,
            "hops": [hop1],
            "related_entity": None,
            "reason": f"Could not resolve hop 1 ({subject}.{relation_predicate}): {hop1['reason']}",
        }

    related_entity = hop1["fact"].object_value
    hop2 = resolve_revision_state(candidate_facts, related_entity, target_predicate)

    return {
        "state": hop2["state"],
        "fact": hop2["fact"],
        "hops": [hop1, hop2],
        "related_entity": related_entity,
        "reason": (
            f"Hop 1 resolved '{subject}.{relation_predicate}' -> '{related_entity}'; "
            f"Hop 2 resolved '{related_entity}.{target_predicate}': {hop2['reason']}"
        ),
    }


# ---------------------------------------------------------------------------
# Word-boundary target extraction (no substring false positives)
# ---------------------------------------------------------------------------
_LOCATION_RE = re.compile(
    r"\b(?:live|lives|living|location|where\s+does|where\s+is|city|moved|relocated|"
    r"reside|resides|based\s+in|currently\s+in|hometown|home\s+town)\b"
)
_JOB_RE = re.compile(
    r"\b(?:job|work\s+as|works\s+as|working\s+as|profession|occupation|employed|"
    r"employment|career|job\s+title|role\s+at|role|title)\b"
)
_PREF_RE = re.compile(
    r"\b(?:prefer|prefers|preference|preferences|like|likes|dark\s+mode|light\s+mode|theme)\b"
)
_RELATION_RE = re.compile(r"\b(?:manager|boss|reports?\s+to)\b")
_TEAM_RE = re.compile(r"\b(?:teammate|colleague|collaborat\w*)\b")
_SALARY_RE = re.compile(r"\b(?:salary|pay|compensation|income|make|earn|earning)\b")
_OWN_RE = re.compile(r"\b(?:own|owns|owned|ownership)\b")


def extract_target_from_query(query: str) -> Tuple[str, str]:
    """Rule-based target extractor. All cues are word-boundary anchored."""
    q = query.lower().strip()

    m = re.search(
        r"\b(?:favorite|favourite)\s+([a-z][a-z0-9\-]*(?:\s+[a-z][a-z0-9\-]*)?)\b", q
    )
    if m:
        slot = re.sub(r"[\s\-]+", " ", m.group(1).strip())
        if slot in {"pet", "dog", "cat", "puppy", "kitten"}:
            return "user", "has_pet"
        if slot in {"dark_mode", "light_mode", "theme", "mode"}:
            return "user", "prefers"
        return "user", f"favorite_{slot}"

    if re.search(r"\b(?:pet|dog|cat|puppy|kitten)\b", q):
        return "user", "has_pet"
    if _LOCATION_RE.search(q):
        return "user", "lives_in"
    if _JOB_RE.search(q) and not _RELATION_RE.search(q) and not _TEAM_RE.search(q):
        return "user", "job"
    if _SALARY_RE.search(q):
        return "user", "salary"
    if _OWN_RE.search(q):
        return "user", "owns"
    if _PREF_RE.search(q):
        return "user", "prefers"
    if _RELATION_RE.search(q) and not _LOCATION_RE.search(q) and not _JOB_RE.search(q):
        return "user", "reports_to"
    if _TEAM_RE.search(q) and not _LOCATION_RE.search(q) and not _JOB_RE.search(q):
        return "user", "collaborates_with"

    return "user", "unknown_property"


def extract_multi_hop_target(query: str) -> Optional[Tuple[str, str, str]]:
    """Lightweight two-hop target matcher for the demo."""
    q = query.lower().strip()
    relation_map = {
        "manager": "reports_to",
        "boss": "reports_to",
        "teammate": "collaborates_with",
        "colleague": "collaborates_with",
    }
    relation_hit = next(
        (v for k, v in relation_map.items() if re.search(rf"\b{k}\b", q)), None
    )
    if not relation_hit:
        return None

    m = re.search(
        r"\b(?:favorite|favourite)\s+([a-z][a-z0-9\-]*(?:\s+[a-z][a-z0-9\-]*)?)\b", q
    )
    if m:
        slot = re.sub(r"[\s\-]+", " ", m.group(1).strip())
        if slot in {"pet", "dog", "cat", "puppy", "kitten"}:
            return "user", relation_hit, "has_pet"
        if slot in {"dark_mode", "light_mode", "theme", "mode"}:
            return "user", relation_hit, "prefers"
        return "user", relation_hit, f"favorite_{slot}"

    target_map = {
        "live": "lives_in", "lives": "lives_in", "location": "lives_in", "city": "lives_in",
        "job": "job", "work": "job", "role": "job",
        "salary": "salary", "pay": "salary", "income": "salary",
        "own": "owns", "owns": "owns", "ownership": "owns",
        "prefer": "prefers", "preference": "prefers", "theme": "prefers",
        "pet": "has_pet", "dog": "has_pet", "cat": "has_pet",
    }
    target_hit = next((v for k, v in target_map.items() if re.search(rf"\b{k}\b", q)), None)
    if relation_hit and target_hit:
        return "user", relation_hit, target_hit
    return None


def pretty_resolution(result: Dict[str, Any]) -> None:
    print(f"State : {result['state']}")
    print(f"Reason: {result['reason']}")
    if result["fact"]:
        f = result["fact"]
        print(f"Answer: {f.object_value}")
        print(f"Fact ID: {f.fact_id}")
        print(f"Timestamp: {f.timestamp.isoformat()}")
        print("Revision chain:")
        for item in result["chain"]:
            print(f"  {item.fact_id}: {item.object_value} @ {item.timestamp.date()}")