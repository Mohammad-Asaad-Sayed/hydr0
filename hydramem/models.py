from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, Tuple

from pydantic import BaseModel


class MemoryState(str, Enum):
    ANSWERABLE = "ANSWERABLE"
    NO_EVIDENCE = "NO_EVIDENCE"
    CONFLICTING = "CONFLICTING"


class MemoryFact(BaseModel):
    """A single typed temporal fact: (subject, predicate) -> object_value at a point in time."""

    fact_id: str
    subject: str
    predicate: str
    object_value: str

    # Event / validity time (not ingestion time)
    timestamp: datetime

    session_id: str
    fact_type: str = "volatile"  # volatile | static | episodic | relation

    # Explicit revision relation (application level)
    supersedes_fact_id: Optional[str] = None

    source_text: Optional[str] = None
    confidence: float = 1.0

    @property
    def property_key(self) -> Tuple[str, str]:
        return (self.subject, self.predicate)

    def to_memory_item(self) -> Dict[str, Any]:
        """Payload for a single memory in context.ingest."""
        return {
            "id": self.fact_id,  # required for BYOG targeting
            "text": self.source_text or f"{self.subject} {self.predicate} {self.object_value}",
            "infer": False,  # we supply the graph ourselves
            "metadata": {
                "fact_id": self.fact_id,
                "subject": self.subject,
                "predicate": self.predicate,
                "object_value": self.object_value,
                "timestamp": self.timestamp.isoformat(),
                "session_id": self.session_id,
                "fact_type": self.fact_type,
                "supersedes_fact_id": self.supersedes_fact_id or "",
                "confidence": self.confidence,
                "property_key": f"{self.subject}:{self.predicate}",
            },
        }

    def to_graph_entities_and_relations(self) -> Dict[str, Any]:
        """Build a BYOG payload fragment: entities + relations (incl. SUPERSEDES edge)."""
        entities = {
            "subj": {"name": self.subject, "type": "ENTITY", "namespace": "memory"},
            "obj": {"name": self.object_value, "type": "VALUE", "namespace": "memory"},
        }

        relations = [
            {
                "source": "subj",
                "target": "obj",
                "predicate": self.predicate.upper(),
                "context": self.source_text
                or f"{self.subject} {self.predicate} {self.object_value}",
                "temporal_details": self.timestamp.date().isoformat(),
            }
        ]

        # Explicit supersession edge (the key graph-native signal)
        if self.supersedes_fact_id:
            entities["prev"] = {
                "name": self.supersedes_fact_id,
                "type": "FACT",
                "namespace": "memory",
            }
            relations.append(
                {
                    "source": "subj",
                    "target": "prev",
                    "predicate": "SUPERSEDES",
                    "context": f"{self.fact_id} supersedes {self.supersedes_fact_id}",
                    "temporal_details": self.timestamp.date().isoformat(),
                }
            )

        return {"entities": entities, "relations": relations}