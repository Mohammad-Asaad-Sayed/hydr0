import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from .models import MemoryFact


# Case-SENSITIVE capitalized-run city matcher. re.I is deliberately NOT used here:
# with re.I, lowercase words like "last week" would bleed into the captured city.
_CITY = r"((?:[A-Z][A-Za-z\-]+)(?:\s+[A-Z][A-Za-z\-]+)*)"

_LOCATION_PATTERNS = [
    (re.compile(rf"\b[Ii]\s+(?:just\s+)?moved\s+to\s+{_CITY}"), "lives_in", "volatile", 0.95),
    (re.compile(rf"\b[Ii]\s+(?:now\s+)?live\s+in\s+{_CITY}"), "lives_in", "volatile", 0.95),
    (re.compile(rf"\b[Ii]\s+relocated\s+to\s+{_CITY}"), "lives_in", "volatile", 0.9),
    (re.compile(rf"\b[Ii](?:'m|\s+am)\s+based\s+in\s+{_CITY}"), "lives_in", "volatile", 0.9),
    (re.compile(rf"\b[Ii]\s+reside\s+in\s+{_CITY}"), "lives_in", "volatile", 0.85),
]

_JOB_STOP = r"(?:\s+at\s|\s+for\s|\s+and\s|\s+but\s|\.|,|!|$)"
_JOB_PATTERNS = [
    (re.compile(rf"\b[Ii]\s+(?:now\s+)?work\s+as\s+(?:a\s+|an\s+)?([a-zA-Z][a-zA-Z\s\-]*?){_JOB_STOP}", re.I), "job", "volatile", 0.9),
    (re.compile(rf"\b[Ii]\s+(?:just\s+)?(?:got|started|took)\s+(?:a\s+|an\s+)?(?:new\s+)?job\s+as\s+(?:a\s+|an\s+)?([a-zA-Z][a-zA-Z\s\-]*?){_JOB_STOP}", re.I), "job", "volatile", 0.9),
    (re.compile(r"\b[Ii](?:'m|\s+am)\s+(?:a\s+|an\s+)?([a-zA-Z\s\-]*(?:engineer|designer|manager|researcher|developer|analyst|scientist))\b", re.I), "job", "volatile", 0.85),
    (re.compile(rf"\b[Mm]y\s+job\s+is\s+(?:a\s+|an\s+)?([a-zA-Z][a-zA-Z\s\-]*?){_JOB_STOP}", re.I), "job", "volatile", 0.85),
]

_PREF_STOP = r"(?:\s+for\s|\s+because\s|\s+when\s|\s+since\s|\.|,|!|$)"
_PREF_PATTERNS = [
    (re.compile(r"\b[Ii]\s+(?:really\s+)?prefer\s+(dark\s+mode|light\s+mode)\b"), "prefers", "static", 0.95),
    (re.compile(rf"\b[Ii]\s+(?:really\s+)?prefer\s+([a-zA-Z][a-zA-Z\s\-]*?){_PREF_STOP}"), "prefers", "static", 0.9),
    (re.compile(r"\b[Ii]\s+like\s+([a-zA-Z\s\-]*?dark\s+mode[a-zA-Z\s\-]*?)(?:\.|,|!|$)"), "prefers", "static", 0.9),
]

ALL_PATTERNS = _LOCATION_PATTERNS + _JOB_PATTERNS + _PREF_PATTERNS

REVISION_CUES = re.compile(
    r"\b(moved|relocated|now live|now work|changed|switched|updated|no longer|instead)\b",
    re.I,
)


def clean_value(raw: str) -> str:
    v = raw.strip().rstrip(".!,")
    v = re.sub(r"\s+", " ", v)
    return v


def extract_facts_from_text(
    text: str,
    *,
    session_id: str,
    timestamp: datetime,
    subject: str = "user",
    fact_id_prefix: str = "auto",
    counter_start: int = 0,
) -> List[MemoryFact]:
    """Rule-based extraction of MemoryFacts from a single dialogue turn."""
    facts: List[MemoryFact] = []
    seen_preds = set()
    idx = counter_start

    open_patterns = [
        (re.compile(r"\b(?:my\s+)?(?:favorite|favourite)\s+([a-z][a-z0-9\-]*(?:\s+[a-z][a-z0-9\-]*)?)\s+is\s+([^.!?,]+)", re.I), "favorite", "static", 0.88),
        (re.compile(r"\b(?:my\s+)?pet\s+(?:is|named)\s+([^.!?,]+)", re.I), "has_pet", "static", 0.92),
        (re.compile(r"\b(?:i|I)\s+(?:have|got)\s+(?:a|an)\s+(?:dog|cat|pet)(?:\s+(?:named|called))?\s+([^.!?,]+)", re.I), "has_pet", "static", 0.92),
        (re.compile(r"\b(?:my\s+)?(?:salary|pay|compensation|income)\s+(?:is|=)\s+([^.!?,]+)", re.I), "salary", "static", 0.9),
        (re.compile(r"\b(?:i|I)\s+(?:make|earn)\s+([^.!?,]+)", re.I), "salary", "static", 0.82),
        (re.compile(r"\b(?:i|I)\s+(?:own)\s+([^.!?,]+)", re.I), "owns", "static", 0.88),
    ]

    for pattern, predicate, fact_type, conf in open_patterns:
        if predicate == "favorite":
            m = pattern.search(text)
            if not m:
                continue
            slot = re.sub(r"[\s\-]+", " ", m.group(1).strip().lower())
            predicate = "has_pet" if slot in {"pet", "dog", "cat", "puppy", "kitten"} else f"favorite_{slot}"
            value = clean_value(m.group(2))
        else:
            if predicate in seen_preds:
                continue
            m = pattern.search(text)
            if not m:
                continue
            value = clean_value(m.group(1))

        if len(value) < 2 or len(value) > 100 or predicate in seen_preds:
            continue
        local_conf = min(1.0, conf + 0.05) if REVISION_CUES.search(text) else conf
        fact_id = f"{fact_id_prefix}_{predicate}_{idx:03d}"
        idx += 1
        seen_preds.add(predicate)
        facts.append(MemoryFact(
            fact_id=fact_id, subject=subject, predicate=predicate, object_value=value,
            timestamp=timestamp, session_id=session_id, fact_type=fact_type,
            supersedes_fact_id=None, source_text=text.strip(), confidence=local_conf,
        ))

    for pattern, predicate, fact_type, conf in ALL_PATTERNS:
        if predicate in seen_preds:
            continue
        m = pattern.search(text)
        if not m:
            continue
        value = clean_value(m.group(1))
        if len(value) < 2 or len(value) > 60:
            continue

        local_conf = conf
        if REVISION_CUES.search(text):
            local_conf = min(1.0, conf + 0.05)

        fact_id = f"{fact_id_prefix}_{predicate}_{idx:03d}"
        idx += 1
        seen_preds.add(predicate)
        facts.append(MemoryFact(
            fact_id=fact_id, subject=subject, predicate=predicate, object_value=value,
            timestamp=timestamp, session_id=session_id, fact_type=fact_type,
            supersedes_fact_id=None, source_text=text.strip(), confidence=local_conf,
        ))

    return facts


def link_supersession(facts: List[MemoryFact]) -> List[MemoryFact]:
    """Link revision chains chronologically. Same-timestamp facts are NOT linked so the
    resolver can flag CONFLICTING instead of silently letting insertion order decide."""
    ordered = sorted(enumerate(facts), key=lambda x: (x[1].timestamp, x[0]))
    latest: Dict[Tuple[str, str], Tuple[str, datetime]] = {}
    result: List[MemoryFact] = []

    for _, f in ordered:
        key = (f.subject, f.predicate)
        prev = latest.get(key)
        if prev and prev[0] != f.fact_id and f.timestamp > prev[1]:
            f = f.model_copy(update={"supersedes_fact_id": prev[0]})
        latest[key] = (f.fact_id, f.timestamp)
        result.append(f)

    return result


def extract_facts_from_sessions(
    sessions: List[Dict],
    *,
    default_subject: str = "user",
    fact_id_prefix: str = "auto",        # ← ADD THIS LINE
) -> List[MemoryFact]:
    all_facts: List[MemoryFact] = []
    counter = 0

    for sess in sessions:
        sid = sess["session_id"]
        ts = sess["timestamp"]
        for turn in sess.get("turns", []):
            if turn.get("role") != "user":
                continue
            text = turn.get("content") or ""
            extracted = extract_facts_from_text(
                text,
                session_id=sid,
                timestamp=ts,
                subject=default_subject,
                fact_id_prefix=fact_id_prefix,   # ← CHANGED (was hardcoded "auto")
                counter_start=counter,
            )
            counter += len(extracted)
            all_facts.extend(extracted)

    return link_supersession(all_facts)

# ===========================================================================
# Optional LLM extraction (Claude Structured Outputs) — fails safe to regex
# ===========================================================================
import os
import json as _json

FACT_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "predicate": {"type": "string"},
                    "object_value": {"type": "string"},
                    "fact_type": {"type": "string", "enum": ["volatile", "static", "episodic"]},
                    "confidence": {"type": "number"},
                },
                "required": ["subject", "predicate", "object_value", "fact_type", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


def extract_facts_from_text_llm(
    text: str, *, session_id: str, timestamp: datetime, subject: str = "user",
    fact_id_prefix: str = "llm", counter_start: int = 0, llm_client=None,
) -> List[MemoryFact]:
    """LLM extraction with hard fallback to regex. Pass llm_client=None for regex-only."""
    if llm_client is None:
        return extract_facts_from_text(
            text, session_id=session_id, timestamp=timestamp, subject=subject,
            fact_id_prefix="auto", counter_start=counter_start,
        )

    try:
        response = llm_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": (
                    f"Extract facts from this message. Return JSON matching this schema: "
                    f"{_json.dumps(FACT_EXTRACTION_SCHEMA)}. Message: {text}"
                ),
            }],
            output_config={"format": {"type": "json_schema", "schema": FACT_EXTRACTION_SCHEMA}},
        )
        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        if not text_blocks:
            raise ValueError("No text block")
        payload = _json.loads(text_blocks[0])
        facts = payload.get("facts", [])
        
        result = []
        for i, item in enumerate(facts):
            pred = re.sub(r"[\s\-]+", "_", str(item.get("predicate", "")).strip().lower())
            val = (item.get("object_value") or "").strip()
            if not pred or not val:
                continue
            result.append(MemoryFact(
                fact_id=f"{fact_id_prefix}_{pred}_{counter_start + i:03d}",
                subject=item.get("subject") or subject,
                predicate=pred,
                object_value=val,
                timestamp=timestamp,
                session_id=session_id,
                fact_type=item.get("fact_type", "volatile"),
                supersedes_fact_id=None,
                source_text=text.strip(),
                confidence=float(item.get("confidence", 0.75)),
            ))
        return link_supersession(result) if result else extract_facts_from_text(
            text, session_id=session_id, timestamp=timestamp, subject=subject,
            fact_id_prefix="auto", counter_start=counter_start,
        )
    except Exception as e:
        print(f"  [extraction] LLM failed ({e}); falling back to regex")
        return extract_facts_from_text(
            text, session_id=session_id, timestamp=timestamp, subject=subject,
            fact_id_prefix="auto", counter_start=counter_start,
        )