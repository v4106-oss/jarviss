from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class ChatMessage:
    role: str
    content: str
    source: str = "chat"
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "source": self.source,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_row(cls, row: dict) -> "ChatMessage":
        created_at = datetime.fromisoformat(row["created_at"])
        return cls(
            role=row["role"],
            content=row["content"],
            source=row.get("source", "chat"),
            metadata=row.get("metadata"),
            created_at=created_at,
        )
