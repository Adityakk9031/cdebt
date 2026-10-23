"""Core Pydantic models. No per-person scoring exists anywhere in these models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AttributionSource(StrEnum):
    GIT_AI = "git-ai"
    AGENT_TRACE = "agent-trace"
    TRAILER = "trailer"
    NONE = "none"  # no evidence: UNKNOWN, never "human-written"


class AuthorKind(StrEnum):
    AI = "ai"
    HUMAN = "human"
    UNKNOWN = "unknown"


class FileAttribution(BaseModel):
    """Per-line attribution of one file at one revision (index 0 == line 1)."""

    model_config = ConfigDict(frozen=False)

    path: str
    kinds: list[AuthorKind]
    sources: list[AttributionSource]
    coarse: bool = False  # True when any AI claim is commit-level (trailer heuristic)

    @property
    def total(self) -> int:
        return len(self.kinds)

    def count(self, kind: AuthorKind) -> int:
        return sum(1 for k in self.kinds if k is kind)

    @property
    def ai_lines(self) -> int:
        return self.count(AuthorKind.AI)

    @property
    def human_lines(self) -> int:
        return self.count(AuthorKind.HUMAN)

    @property
    def unknown_lines(self) -> int:
        return self.count(AuthorKind.UNKNOWN)

    @property
    def source(self) -> AttributionSource:
        """Highest-priority source that made any claim; `none` if every line is unknown."""
        present = {
            s for s, k in zip(self.sources, self.kinds, strict=True) if k is not AuthorKind.UNKNOWN
        }
        for s in (
            AttributionSource.GIT_AI,
            AttributionSource.AGENT_TRACE,
            AttributionSource.TRAILER,
        ):
            if s in present:
                return s
        return AttributionSource.NONE

    def source_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s, k in zip(self.sources, self.kinds, strict=True):
            if k is not AuthorKind.UNKNOWN:
                out[s.value] = out.get(s.value, 0) + 1
        return out


class CodeUnit(BaseModel):
    """The thing understanding attaches to: a function/method, or the module residual."""

    path: str
    symbol: str  # qualified, e.g. "Cls.method"; "<module>" for residual; "<file>" fallback
    ranges: list[tuple[int, int]]  # inclusive 1-indexed line ranges
    content_hash: str

    def lines(self) -> list[int]:
        return [n for a, b in self.ranges for n in range(a, b + 1)]


class Probe(BaseModel):
    id: str
    kind: Literal[
        "runtime",
        "callgraph",
        "mutation",
        "constant",
        "return_value",
        "branch_taken",
        "variable_at_line",
    ]
    path: str
    symbol: str
    question: str
    options: list[str] = Field(default_factory=list)
    answer: str  # canonical, computed - never LLM-judged (JSON list when multi)
    multi: bool = False  # multi-select: answer is a JSON list of option strings
    verified_by: Literal["sandbox-exec", "scip-index", "mutation-run", "execution-trace"]
    evidence: dict[str, object] = Field(default_factory=dict)
    seed: int = 0
    user_answer: str | None = None
    correct: bool | None = None
    # v1.1: who proposed the question (`builtin` or `agent:<name>`), the sha256 of an agent
    # harness, and the difficulty label for the adaptive quiz. None = not recorded (older files).
    proposer: str | None = None
    harness_hash: str | None = None
    difficulty: Literal["easy", "medium", "hard"] | None = None


class Record(BaseModel):
    """Understanding record, bound to the content hash of the unit it covers."""

    # unknown fields (from a newer minor version) are preserved on save, not dropped
    model_config = ConfigDict(extra="allow")

    unit_path: str
    unit_symbol: str
    content_hash: str
    probes: list[Probe] = Field(default_factory=list)
    passed: bool
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    audit_label: str | None = None  # audit only; never aggregated or reported per person
    # `verified`: execution-verified probes. `attested`: a human signed off; NON-VERIFIED.
    kind: Literal["verified", "attested"] = "verified"
    note: str | None = None  # attestation note (what was read / why it is fine)
    # attested records only, decided at attest time: why quiz could not clear this unit.
    scope: Literal["module", "function-unquizzable", "function-quizzable"] | None = None
    # v1.1: why the CODE record has this verdict, e.g. verified, follow_up_failed,
    # score_too_low. Describes the record, never a person. None on older records.
    status_reason: str | None = None

    @property
    def probes_asked(self) -> int:
        return len(self.probes)

    @property
    def probes_correct(self) -> int:
        return sum(1 for p in self.probes if p.correct)


class FileDebt(BaseModel):
    path: str
    total_lines: int
    ai_lines: int
    human_lines: int
    unknown_lines: int
    understood_ai_lines: int  # AI lines in VERIFIED units
    attested_ai_lines: int = 0  # AI lines in ATTESTED (NON-VERIFIED) units
    ai_share: float
    understood_share: float
    churn: float
    blast_radius: float
    blast_source: Literal["scip", "approx", "none"]
    score: float
    attribution_source: AttributionSource
    source_counts: dict[str, int] = Field(default_factory=dict)
    coarse: bool = False
    ai_units: dict[str, int] = Field(default_factory=dict)  # unit symbol -> AI lines in it


class Violation(BaseModel):
    path: str
    symbol: str
    ai_lines: list[int]
    # attested = NON-VERIFIED, not accepted here; verified = understood; recheck_failed = a record
    # exists but CI re-execution did not confirm it
    status: Literal["none", "stale", "attested", "verified", "recheck_failed"]
    attribution_source: AttributionSource
    ranges: list[tuple[int, int]] = Field(default_factory=list)  # unit line ranges (1-indexed)
    reason: str | None = None  # why the CODE is not understood; never about a person


class GateResult(BaseModel):
    passed: bool  # the verdict; the exit code only reflects it when --enforce is given
    enforced: bool = False  # False = advisory: report, never fail the build
    base: str
    head: str
    ai_lines_unverified: int
    max_ai_lines: int
    strict_unknown: bool
    violations: list[Violation] = Field(default_factory=list)
    unknown_files: list[str] = Field(default_factory=list)
    require: Literal["default", "verified", "attested"] = "default"
    attested_accepted: list[Violation] = Field(default_factory=list)  # NON-VERIFIED, accepted
    attested_accepted_lines: int = 0
    understood: list[Violation] = Field(default_factory=list)  # cleared by verified records
    rechecked: bool = False  # records were re-executed in this run
    unknown_changed_lines: int = 0
    attribution_sources: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
