"""Session persistence utilities (AgentSession serialization)."""

import json
from pathlib import Path

from agent_framework import AgentSession


def load_session(path: Path) -> AgentSession:
    """Load and restore an AgentSession from a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return AgentSession.from_dict(data)


def save_session(path: Path, session: AgentSession) -> None:
    """Save an AgentSession to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)


__all__ = ["load_session", "save_session"]
