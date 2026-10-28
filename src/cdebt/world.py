"""The fake world a function runs in: a scenario the agent may PROPOSE and cdebt executes.

A scenario is data, never code: environment variables, files that exist before the call, canned
answers for HTTP requests, canned results for shell commands and the clock. Inside the sandbox
every one of those is faked in-process (nothing real is contacted or spawned), so functions that
read the environment, call an API, run a command or write a file can be run for real and the
answer comes from the run. The agent still never sees an answer and the scenario never decides
one: it only describes the world the code ran in, and the question text says so."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator

from cdebt.sandbox import _import_aliases

MAX_RULES = 20
MAX_FILES = 20
MAX_ENV = 30
MAX_TEXT = 20_000


class HttpRule(BaseModel):
    """First rule whose `url` (substring or "*") and optional `method` match answers a request."""

    url: str = "*"
    method: str | None = None
    status: int = Field(200, ge=100, le=599)
    body: Any = None  # a JSON value, sent as the response body
    text: str | None = Field(None, max_length=MAX_TEXT)  # or a plain-text body
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError("url pattern too long")
        return v


class CmdRule(BaseModel):
    """First rule whose `cmd` (substring of the whole command line, or "*") matches answers it."""

    cmd: str = "*"
    returncode: int = Field(0, ge=-255, le=255)
    stdout: str = Field("", max_length=MAX_TEXT)
    stderr: str = Field("", max_length=MAX_TEXT)


class World(BaseModel):
    env: dict[str, str] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)  # repo-relative path -> text
    http: list[HttpRule] = Field(default_factory=list)
    commands: list[CmdRule] = Field(default_factory=list)
    now: str | None = None  # ISO 8601, e.g. "2024-03-01T12:00:00Z"; default 2020-01-01T00:00:00Z

    @field_validator("env")
    @classmethod
    def _env(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > MAX_ENV or any(len(k) > 100 or len(x) > 1000 for k, x in v.items()):
            raise ValueError("too many or too long environment variables")
        return v

    @field_validator("files")
    @classmethod
    def _files(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > MAX_FILES:
            raise ValueError(f"at most {MAX_FILES} files")
        for path, text in v.items():
            p = PurePosixPath(path)
            if p.is_absolute() or ".." in p.parts or not p.parts or "\x00" in path:
                raise ValueError(f"file path must be relative and stay inside the repo: {path!r}")
            if len(text) > MAX_TEXT:
                raise ValueError(f"file {path!r} is too long")
        return v

    @field_validator("http", "commands")
    @classmethod
    def _rules(cls, v: list[Any]) -> list[Any]:
        if len(v) > MAX_RULES:
            raise ValueError(f"at most {MAX_RULES} rules")
        return v

    @field_validator("now")
    @classmethod
    def _now(cls, v: str | None) -> str | None:
        if v is not None:
            epoch(v)
        return v


def epoch(now: str) -> float:
    try:
        t = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"`now` must be an ISO 8601 time, got {now!r}") from e
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.timestamp()


def runner_spec(w: World) -> dict[str, Any]:
    """The scenario in the form the sandbox runner reads (bodies already serialised)."""
    http = []
    for r in w.http:
        headers = {k.lower(): v for k, v in r.headers.items()}
        if r.text is not None:
            body = r.text
        elif r.body is not None:
            body = json.dumps(r.body, sort_keys=True)
            headers.setdefault("content-type", "application/json")
        else:
            body = ""
        http.append(
            {
                "url": r.url,
                "method": r.method,
                "status": r.status,
                "body_text": body,
                "headers": headers,
            }
        )
    return {
        "env": dict(w.env),
        "http": http,
        "commands": [c.model_dump() for c in w.commands],
    }


def note(used: list[str], w: World, fake_epoch: float, seed: int) -> str:
    """Suffix for a question: only the parts of the fake world the run actually touched."""
    parts: list[str] = []
    if "clock" in used:
        when = datetime.fromtimestamp(fake_epoch, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        parts.append(f"the clock is fixed at {when}")
    if "random" in used:
        parts.append(f"random numbers, UUIDs and secrets are seeded with {seed}")
    if "env" in used:
        parts.append(
            "environment variables are " + ", ".join(f"{k}={v!r}" for k, v in sorted(w.env.items()))
            if w.env
            else "no environment variables are set"
        )
    if "http" in used:
        rules = "; ".join(
            f"{(r.method + ' ') if r.method else ''}{r.url} -> {r.status}"
            + (f" {json.dumps(r.body, sort_keys=True)}" if r.body is not None else "")
            + (f" {r.text!r}" if r.text is not None else "")
            for r in w.http
        )
        tail = f"{rules}; anything else" if rules else "every request"
        parts.append(f"HTTP requests go to a fake server ({tail} gets 200 and {{}})")
    if "commands" in used:
        rules = "; ".join(f"`{c.cmd}` -> exit {c.returncode}" for c in w.commands)
        tail = f"{rules}; anything else" if rules else "every command"
        parts.append(f"shell commands are faked ({tail} exits 0 with no output)")
    if "listing" in used:
        parts.append("directory listings show a scratch copy holding only the repo's tracked files")
    if w.files:
        parts.append("these files exist first: " + ", ".join(sorted(w.files)))
    return f" (in the test sandbox {'; '.join(parts)})" if parts else ""


def relevant_uses(source: str, used: list[str]) -> list[str]:
    """The fake-world parts worth telling the reader about: stdlib internals also read the clock
    and the environment, so clock/random/env count only when the target's module imports them."""
    try:
        mods = set(_import_aliases(ast.parse(source)).values())
    except SyntaxError:
        return used
    need = {
        "clock": {"time", "datetime"},
        "random": {"random", "uuid", "secrets", "os"},
        "env": {"os"},
    }
    return [u for u in used if u not in need or mods & need[u]]
