"""The USER's agent choice: ~/.config/cdebt/config.toml (or $XDG_CONFIG_HOME, $CDEBT_USER_CONFIG).
Never read from the repository: a repo must not be able to pick which program cdebt launches."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel


class AgentChoice(BaseModel):
    name: str  # claude_code | codex | cursor | command | builtin
    command: str | None = None  # only for `command`: argv string; $CDEBT_MCP_CMD is provided


def path() -> Path:
    override = os.environ.get("CDEBT_USER_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "cdebt" / "config.toml"


def load() -> AgentChoice | None:
    p = path()
    if not p.is_file():
        return None
    data = tomllib.loads(p.read_text()).get("agent")
    return AgentChoice.model_validate(data) if data else None


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(choice: AgentChoice) -> Path:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = f"[agent]\nname = {_q(choice.name)}\n"
    if choice.command:
        body += f"command = {_q(choice.command)}\n"
    p.write_text(body)
    return p


def clear() -> bool:
    p = path()
    if p.is_file():
        p.unlink()
        return True
    return False
