"""CI re-execution of recorded probes, so a hand-edited record cannot clear code.

A record is accepted as VERIFIED only if every probe it holds that can be re-executed still gets
the recorded answer when run again in the CI sandbox against the code at the PR head, the recorded
grading is consistent, and it holds at least one probe. Call-graph and mutation probes are not
re-executed here (UNVERIFIED: they depend on an index or on the project's tests); a record that
holds only those is accepted but flagged.

Limit (docs/LIMITS.md): this catches wrong or inconsistent answers, not someone who never sat the
quiz and wrote correct answers into a record by hand."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from cdebt.agents.probes import Proposal, Rejected, execute
from cdebt.config import Config
from cdebt.models import Probe, Record
from cdebt.probes.base import _norm, grade


@dataclass
class RecheckResult:
    ok: bool
    reason: str | None = None
    reexecuted: int = 0
    not_reexecutable: int = 0


def _spec_for(p: Probe) -> Proposal | None:
    spec = p.evidence.get("spec")
    if isinstance(spec, dict):
        try:
            return Proposal.model_validate(spec)
        except ValueError:
            return None
    # records written before v1.1: rebuild the proposal from the evidence
    if p.kind == "runtime" and "args" in p.evidence:
        try:
            args = list(ast.literal_eval(str(p.evidence["args"])))
        except (ValueError, SyntaxError):
            return None
        return Proposal(target=p.symbol, kind="return_value", call_inputs={"args": args})
    if p.kind == "constant" and "name" in p.evidence:
        return Proposal(target=p.symbol, kind="constant_value", name=str(p.evidence["name"]))
    return None


def verify_record(repo: Path, rel: str, record: Record, cfg: Config, source: str) -> RecheckResult:
    if record.kind != "verified":
        return RecheckResult(True)
    if not record.probes:
        return RecheckResult(False, "verified record holds no probes")
    res = RecheckResult(True)
    for p in record.probes:
        if p.user_answer is not None and bool(grade(p, p.user_answer)) != bool(p.correct):
            return RecheckResult(False, f"recorded grading of `{p.question[:60]}` is inconsistent")
        if p.kind in ("callgraph", "mutation"):
            res.not_reexecutable += 1
            continue
        prop = _spec_for(p)
        if prop is None:
            res.not_reexecutable += 1
            continue
        try:
            fresh = execute(repo, rel, prop, cfg, proposer="recheck", source=source)
        except Rejected as e:
            return RecheckResult(False, f"could not re-execute `{p.question[:60]}`: {e}")
        if _norm(fresh.answer) != _norm(p.answer):
            return RecheckResult(
                False, f"recorded answer for `{p.question[:60]}` does not match re-execution"
            )
        res.reexecuted += 1
    return res
