"""cdebt command line. Measures code, never people."""

from __future__ import annotations

import contextlib
import functools
import json
import math
import os
import random
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from cdebt import gitutil
from cdebt.config import load_config
from cdebt.mapper import MapResult, compute_map
from cdebt.scip import ScipIndex, index_files, load_all

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Measure, test and pay down comprehension debt. Measures code, not people.",
)
console = Console()


@app.callback()
def _main() -> None:
    """cdebt: comprehension-debt toolkit."""


TrustedOpt = Annotated[
    bool,
    typer.Option(
        "--trusted",
        help="I trust this repository's code: run probes with the host sandbox instead of "
        "requiring strong isolation (Docker, or macOS sandbox-exec). Also CDEBT_TRUSTED=1. "
        "Never read from the repository itself.",
    ),
]


NoCacheOpt = Annotated[
    bool, typer.Option("--no-cache", help="ignore and do not write .cdebt/cache (CDEBT_NO_CACHE=1)")
]


def _apply_cache(no_cache: bool) -> None:
    if no_cache:
        os.environ["CDEBT_NO_CACHE"] = "1"


def _apply_isolation(trusted: bool) -> None:
    from cdebt import sandbox

    try:
        sandbox.require_posix()
    except sandbox.SandboxUnavailable as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    sandbox.set_default_isolation("host" if sandbox.is_trusted(trusted) else "strong")


@contextlib.contextmanager
def _sandbox_errors() -> Iterator[None]:
    from cdebt.sandbox import SandboxUnavailable

    try:
        yield
    except SandboxUnavailable as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None


RepoOpt = Annotated[Path, typer.Option("--repo", help="Repository path.")]


def _repo(p: Path) -> Path:
    try:
        return gitutil.repo_root(p)
    except gitutil.GitError:
        typer.echo(f"error: {p} is not a git repository", err=True)
        raise typer.Exit(2) from None


def _rel(file: Path, repo: Path) -> str:
    """Repo-relative path of `file`: relative to the cwd if it exists there, else to --repo."""
    cand = file if file.is_absolute() else Path.cwd() / file
    if not cand.exists() and not file.is_absolute():
        cand = repo / file
    try:
        return cand.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        typer.echo(f"error: {file} is not inside the repository {repo}", err=True)
        raise typer.Exit(2) from None


def command(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """`app.command` plus uniform handling of unreadable `.cdebt/understanding.json`
    (corrupt, or written by a newer cdebt): `error: ...`, exit 2, no traceback."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*a: Any, **k: Any) -> Any:
            from cdebt.store import StoreError

            try:
                return fn(*a, **k)
            except StoreError as e:
                typer.echo(f"error: {e}", err=True)
                raise typer.Exit(2) from None

        return app.command(name)(wrapper)

    return deco


def _scip(repo: Path, build: bool) -> ScipIndex | None:
    from cdebt.scip import build_indexes

    if build and not index_files(repo):
        build_indexes(repo, ("python",))
    return load_all(repo) if index_files(repo) else None


def render_map(res: MapResult, top: int) -> None:
    s = res.summary
    t = Table(
        title=f"cdebt map @ {s.rev}",
        caption="score = ai_share x (1-understood) x (1+w*churn) x blast; understood = verified "
        "+ weight x attested. `attested` = human sign-off, NON-VERIFIED. `unknown` attribution "
        "is NOT human-written.",
    )
    for c, j in (
        ("file", "left"),
        ("AI%", "right"),
        ("verified%", "right"),
        ("attested% (NON-VERIFIED)", "right"),
        ("unknown%", "right"),
        ("churn", "right"),
        ("blast", "right"),
        ("blast src", "left"),
        ("score", "right"),
        ("attribution", "left"),
    ):
        t.add_column(c, justify=j)  # type: ignore[arg-type]
    for f in res.files[:top]:
        t.add_row(
            f.path,
            f"{f.ai_share:.0%}",
            f"{f.understood_ai_lines / f.ai_lines:.0%}" if f.ai_lines else "-",
            f"{f.attested_ai_lines / f.ai_lines:.0%}" if f.ai_lines else "-",
            f"{f.unknown_lines / f.total_lines:.0%}",
            f"{f.churn:.2f}",
            f"{f.blast_radius:.2f}",
            f.blast_source,
            f"{f.score:.3f}",
            f.attribution_source.value + ("*" if f.coarse else ""),
        )
    console.print(t)
    srcs = ", ".join(f"{k}={v}" for k, v in s.attribution_sources.items()) or "none (all unknown)"
    und = "n/a" if s.understood_ai_share is None else f"{s.understood_ai_share:.0%}"
    att = "n/a" if s.attested_ai_share is None else f"{s.attested_ai_share:.0%}"
    lo, hi = s.understood_units_ci95
    console.print(
        f"[bold]Summary[/] files={s.files} lines={s.total_lines} | AI={s.ai_share:.1%} "
        f"({s.ai_lines}) unknown={s.unknown_share:.1%} ({s.unknown_lines}) | "
        f"AI lines verified: {und} | attested (NON-VERIFIED): {att} | AI units verified: "
        f"{s.units_understood}/{s.units_with_ai} (95% CI {lo:.0%}-{hi:.0%}), attested "
        f"{s.units_attested} | attribution sources: {srcs}"
    )
    if s.coarse_attribution:
        console.print("[yellow]* coarse: commit-level trailer heuristic, not line-level.[/]")


@command("map")
def map_cmd(
    repo: RepoOpt = Path("."),
    rev: str = "HEAD",
    json_out: Annotated[bool, typer.Option("--json")] = False,
    top: int = 20,
    no_scip: Annotated[bool, typer.Option("--no-scip")] = False,
    build_scip: Annotated[bool, typer.Option("--build-scip")] = False,
    no_cache: NoCacheOpt = False,
) -> None:
    """Which files hold the most AI-written code nobody has verifiably understood?"""
    r = _repo(repo)
    _apply_cache(no_cache)
    idx = None if no_scip else _scip(r, build_scip)
    res = compute_map(r, load_config(r), rev, scip_idx=idx)
    if json_out:
        typer.echo(json.dumps(json.loads(res.model_dump_json()), indent=2))
    else:
        render_map(res, top)


@command("index")
def index_cmd(
    repo: RepoOpt = Path("."),
    language: Annotated[
        str, typer.Option(help="python, typescript or all (detected from tracked files)")
    ] = "all",
) -> None:
    """Build the SCIP index/indexes (needs node/npx; writes only .cdebt/cache/)."""
    from cdebt.scip import LANG_EXT, build_indexes

    r = _repo(repo)
    if language == "all":
        files = gitutil.ls_files(r)
        langs = tuple(lg for lg, ext in LANG_EXT.items() if any(f.endswith(ext) for f in files))
    else:
        langs = (language,)
    written = build_indexes(r, langs) if langs else []
    if not written:
        typer.echo("error: indexing failed (need node/npx and network for the indexer)", err=True)
        raise typer.Exit(2)
    for w in written:
        typer.echo(f"wrote {w}")


def _quiz_pr(r: Path, pr: int, trusted: bool) -> None:
    """Take the quiz for an existing PR, push the notes, and ask CI to re-check."""
    import shutil
    import subprocess

    from cdebt import hook, notes

    gh = os.environ.get("CDEBT_GH") or shutil.which("gh")
    if not gh:
        typer.echo("error: `gh` (GitHub CLI) is needed for --pr", err=True)
        raise typer.Exit(2)
    p = subprocess.run(
        [gh, "pr", "view", str(pr), "--json", "baseRefName,headRefOid"],
        capture_output=True,
        text=True,
        cwd=r,
    )
    if p.returncode != 0:
        typer.echo(f"error: gh could not read PR {pr}: {p.stderr.strip()[-200:]}", err=True)
        raise typer.Exit(2)
    info = json.loads(p.stdout)
    head = gitutil.resolve(r, "HEAD")
    if head != info["headRefOid"]:
        typer.echo(
            f"error: check out the PR branch first (HEAD is {head[:10]}, the PR head is "
            f"{info['headRefOid'][:10]})",
            err=True,
        )
        raise typer.Exit(2)
    base = f"origin/{info['baseRefName']}"
    _apply_isolation(trusted)
    with _sandbox_errors():
        s = hook.quiz_changes(r, load_config(r), base, head, console.print)
    for line in s.skipped:
        console.print(f"[dim]{line}[/]")
    if not s.wrote_notes:
        console.print("nothing to record")
        return
    ok, msg = notes.push_notes(r, "origin")
    console.print("notes pushed" if ok else f"[red]could not push notes: {msg}[/]")
    if ok:
        c = subprocess.run(
            [gh, "pr", "comment", str(pr), "--body", "/cdebt recheck"],
            capture_output=True,
            text=True,
            cwd=r,
        )
        console.print(
            "asked CI to re-check"
            if c.returncode == 0
            else "comment /cdebt recheck on the PR yourself"
        )


@command("quiz")
def quiz_cmd(
    file: Annotated[Path | None, typer.Argument()] = None,
    repo: RepoOpt = Path("."),
    pr: Annotated[
        int | None, typer.Option("--pr", help="quiz the AI-written code of an existing PR")
    ] = None,
    n: int = 5,
    seed: int = 0,
    kinds: Annotated[
        str, typer.Option(help="comma list: runtime,callgraph,constant,mutation")
    ] = "runtime,callgraph,constant",
    label: Annotated[
        str | None, typer.Option(help="audit label, never reported per person")
    ] = None,
    answers: Annotated[Path | None, typer.Option(help="JSON {probe_id: answer}")] = None,
    emit: Annotated[Path | None, typer.Option(help="write questions JSON and exit")] = None,
    expires_days: Annotated[int | None, typer.Option()] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="list every skipped unit")] = False,
    trusted: TrustedOpt = False,
    no_agent: Annotated[
        bool, typer.Option("--no-agent", help="do not use the connected coding agent")
    ] = False,
    simple: Annotated[
        bool,
        typer.Option("--simple", help="fixed question list (no adaptive rounds or follow-ups)"),
    ] = False,
    no_timer: Annotated[
        bool,
        typer.Option("--no-timer", help="no countdown on follow-ups (this weakens the check)"),
    ] = False,
    agent_max: Annotated[int, typer.Option(help="max proposals per function")] = 8,
    agent_timeout: Annotated[float, typer.Option(help="max seconds for the agent")] = 90.0,
) -> None:
    """Ask questions whose answers come from EXECUTING the code, then record understanding."""
    from cdebt.agents import userconfig
    from cdebt.models import Probe
    from cdebt.probes.base import LETTERS
    from cdebt.quiz import build_quiz, dirty, record_results

    r = _repo(repo)
    if pr is not None:
        _quiz_pr(r, pr, trusted)
        return
    if file is None:
        typer.echo("error: give a FILE or --pr NUMBER", err=True)
        raise typer.Exit(2)
    cfg = load_config(r)
    rel = _rel(file, r)
    ks = tuple(k.strip() for k in kinds.split(",") if k.strip())
    _apply_isolation(trusted)
    adaptive_mode = not simple and answers is None and emit is None and not json_out
    with _sandbox_errors():
        choice = None if no_agent else userconfig.load()
        probes, units, skips = build_quiz(
            r, rel, cfg, n=max(n, 12) if adaptive_mode else n, seed=seed, kinds=ks, agent=choice,
            per_unit=6 if adaptive_mode else 2,
            agent_per_target=agent_max, agent_timeout=agent_timeout,
        )  # fmt: skip
    if skips and verbose:
        for s in skips:
            console.print(f"[dim]skipped: {s}[/]")
    elif skips:
        console.print(
            f"[dim]{len(skips)} unit(s)/probe kinds skipped (side effects, unsupported "
            "types or signatures, no index entry); use --verbose to list.[/]"
        )
    if not probes:
        typer.echo("no probes could be generated for this file (see skipped reasons)", err=True)
        raise typer.Exit(1)

    def public(p: Probe) -> dict[str, object]:
        return {
            "id": p.id,
            "kind": p.kind,
            "unit": p.symbol,
            "question": p.question,
            "options": p.options,
            "multi": p.multi,
        }

    if emit:
        emit.write_text(json.dumps([public(p) for p in probes], indent=2))
        typer.echo(f"wrote {len(probes)} questions to {emit} (answers are NOT included)")
        return
    if answers and not answers.is_file():
        typer.echo(f"error: answers file not found: {answers}", err=True)
        raise typer.Exit(2)
    given: dict[str, str] = json.loads(answers.read_text()) if answers else {}
    iso = sorted({str(p.evidence.get("isolation")) for p in probes if "isolation" in p.evidence})
    if iso:
        console.print(f"[dim]sandbox: {', '.join(iso)}[/]")
    if dirty(r, rel):
        console.print(
            "[yellow]working copy differs from HEAD: the record binds to the "
            "current text and applies once that text is committed.[/]"
        )
    if adaptive_mode:
        _adaptive_quiz(r, rel, cfg, units, probes, label, expires_days, no_timer)
        return
    for i, p in enumerate(probes, 1):
        if p.id in given:
            continue
        console.print(f"\n[bold]Q{i}/{len(probes)}[/] [{p.kind}] {p.question}")
        for j, o in enumerate(p.options):
            console.print(f"  {LETTERS[j]}) {o}")
        hint = "letters, comma-separated (or 'none')" if p.multi else "letter or value"
        given[p.id] = typer.prompt(f"answer ({hint})")
    recs = record_results(r, rel, cfg, units, probes, given, label=label, expires_days=expires_days)
    if json_out:
        typer.echo(json.dumps([json.loads(x.model_dump_json()) for x in recs], indent=2))
        return
    for p in probes:
        mark = "[green]correct[/]" if p.correct else "[red]wrong[/]"
        console.print(f"{p.symbol}: {mark} - answer was {p.answer} (verified by {p.verified_by})")
    for x in recs:
        console.print(
            f"[bold]{x.unit_symbol}[/]: {x.probes_correct}/{x.probes_asked} -> "
            f"{'UNDERSTOOD' if x.passed else 'not yet'} (bound to hash "
            f"{x.content_hash[:10]})"
        )
    console.print("Quizzes show visibility, not proof: they cannot stop answers being looked up.")


def _adaptive_quiz(
    r: Path,
    rel: str,
    cfg: Any,
    units: list[Any],
    probes: list[Any],
    label: str | None,
    expires_days: int | None,
    no_timer: bool,
) -> None:
    from cdebt import adaptive
    from cdebt.probes.base import LETTERS
    from cdebt.store import Store

    policy = adaptive.load_policy(r)
    if no_timer:
        policy = policy.model_copy(update={"timer": False})
    if not policy.timer:
        console.print("[yellow]timer off: follow-ups are untimed, which weakens the check.[/]")
    source = (r / rel).read_text()
    count = {"n": 0}

    def ask(p: Any, timeout: float | None) -> tuple[str | None, float]:
        count["n"] += 1
        tag = f" [yellow](follow-up, {timeout:.0f} s)[/]" if timeout is not None else ""
        console.print(f"\n[bold]Q{count['n']}[/] [{p.difficulty}] {p.question}{tag}")
        for j, o in enumerate(p.options):
            console.print(f"  {LETTERS[j]}) {o}")
        return adaptive.tty_ask(p, timeout)

    outcomes = adaptive.run_adaptive(r, rel, cfg, units, source, probes, ask, policy)
    store = Store(r)
    adaptive.record_outcomes(rel, units, outcomes, store=store, expires_days=expires_days)
    for o in outcomes:
        for p in o.asked:
            mark = "[green]correct[/]" if p.correct else "[red]wrong[/]"
            console.print(
                f"{o.symbol}: {mark} - answer was {p.answer} (verified by {p.verified_by})"
            )
        verdict = "UNDERSTOOD" if o.cleared else "NOT UNDERSTOOD"
        console.print(f"[bold]{o.symbol}[/]: {verdict} ({o.reason}, {o.rounds} round(s))")
    console.print("Quizzes show visibility, not proof: they cannot stop answers being looked up.")


@command("gate")
def gate_cmd(
    repo: RepoOpt = Path("."),
    base: str = "origin/main",
    head: str = "HEAD",
    max_ai_lines: int = 0,
    strict_unknown: Annotated[bool, typer.Option("--strict-unknown")] = False,
    require: Annotated[
        str | None,
        typer.Option(
            "--require",
            help="verified: only execution-verified counts. attested: NON-VERIFIED sign-offs "
            "count everywhere. Default: attested accepted for module-level code and for "
            "functions quiz cannot verify; verified required otherwise.",
        ),
    ] = None,
    enforce: Annotated[
        bool,
        typer.Option(
            "--enforce",
            help="exit 1 when the gate fails. Without it the gate is ADVISORY: it reports the "
            "verdict (and the PR comment says so) but always exits 0.",
        ),
    ] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    markdown: Annotated[Path | None, typer.Option(help="write PR-comment markdown")] = None,
    recheck: Annotated[
        bool,
        typer.Option(
            "--recheck",
            help="re-execute recorded probes in the sandbox and reject records whose answers do "
            "not match (needs Docker or macOS strict isolation, or --trusted)",
        ),
    ] = False,
    pr: Annotated[int | None, typer.Option(help="PR number, shown in the comment hint")] = None,
    no_notes: Annotated[
        bool, typer.Option("--no-notes", help="ignore refs/notes/cdebt records")
    ] = False,
    trusted: TrustedOpt = False,
) -> None:
    """CI check on AI-authored changed lines in code nobody has understood. Advisory by default;
    pass --enforce to make a failing verdict exit 1 (exit 2 is always a tool error)."""
    from cdebt.gate import run_gate, to_markdown

    r = _repo(repo)
    if recheck:
        _apply_isolation(trusted)
    try:
        with _sandbox_errors():
            res = run_gate(
                r,
                load_config(r),
                base,
                head,
                max_ai_lines=max_ai_lines,
                strict_unknown=strict_unknown,
                require=require or "default",
                use_notes=not no_notes,
                recheck=recheck,
            )
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    except gitutil.GitError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    res.enforced = enforce
    if markdown:
        markdown.write_text(to_markdown(res, pr))
    if json_out:
        typer.echo(json.dumps(json.loads(res.model_dump_json()), indent=2))
    else:
        console.print(
            f"[bold]cdebt gate: {'PASS' if res.passed else 'FAIL'}"
            f"{'' if enforce else ' (advisory, exit 0; use --enforce to fail)'}[/] "
            f"AI lines in un-understood units: {res.ai_lines_unverified} "
            f"(max {res.max_ai_lines}) | sources: {res.attribution_sources or 'none'}"
        )
        for v in res.violations[:20]:
            console.print(
                f"  {v.path}::{v.symbol}  {len(v.ai_lines)} AI lines "
                f"\\[{v.status}, {v.attribution_source.value}]"
            )
        if res.attested_accepted_lines:
            console.print(
                f"[yellow]{res.attested_accepted_lines} AI line(s) accepted on ATTESTATION "
                "(NON-VERIFIED).[/]"
            )
        for n in res.notes:
            console.print(f"[yellow]{escape(n)}[/]")
    raise typer.Exit(0 if (res.passed or not enforce) else 1)


@command("attest")
def attest_cmd(
    file: Path,
    note: Annotated[str, typer.Option("--note", help="what you read / why it is fine (required)")],
    repo: RepoOpt = Path("."),
    unit: Annotated[
        list[str] | None, typer.Option("--unit", help="unit symbol (repeatable); default: all")
    ] = None,
    label: Annotated[
        str | None, typer.Option(help="audit label, never reported per person")
    ] = None,
    expires_days: Annotated[int | None, typer.Option()] = None,
    force: Annotated[
        bool, typer.Option("--force", help="attest even functions that quiz could verify")
    ] = False,
    trusted: TrustedOpt = False,
) -> None:
    """Record a human sign-off. NON-VERIFIED: nothing is executed or checked. Hash-bound, so it
    expires when the code changes. Use for module-level or unquizzable code."""
    from cdebt.quiz import attest
    from cdebt.sandbox import SandboxUnavailable

    r = _repo(repo)
    rel = _rel(file, r)
    _apply_isolation(trusted)
    try:
        recs, notes = attest(
            r,
            rel,
            load_config(r),
            note,
            symbols=unit,
            label=label,
            expires_days=expires_days,
            force=force,
        )
    except (ValueError, FileNotFoundError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    except SandboxUnavailable as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    for n in notes:
        console.print(escape(n))
    console.print(
        f"[bold]{len(recs)} unit(s) attested[/] (NON-VERIFIED human sign-off; counts at weight "
        f"{load_config(r).attested_weight:g} in the map, accepted by the default gate only for "
        "module-level or unquizzable code). Commit .cdebt/understanding.json."
    )
    if not recs:
        raise typer.Exit(1)


@command("coverage")
def coverage_cmd(
    repo: RepoOpt = Path("."),
    top_n: Annotated[int, typer.Option("--top-n", help="number of highest-debt files")] = 25,
    rev: str = "HEAD",
    json_out: Annotated[bool, typer.Option("--json")] = False,
    no_scip: Annotated[bool, typer.Option("--no-scip")] = False,
    trusted: TrustedOpt = False,
    no_cache: NoCacheOpt = False,
) -> None:
    """What fraction of the top-N debt files can be VERIFIED, only ATTESTED, or not quizzed at
    all (incl. TypeScript)? Run this before trusting the gate."""
    from cdebt.coverage import compute_coverage

    r = _repo(repo)
    _apply_cache(no_cache)
    _apply_isolation(trusted)
    with _sandbox_errors():
        res = compute_coverage(
            r, load_config(r), top_n, rev, scip_idx=None if no_scip else _scip(r, False)
        )
    if json_out:
        typer.echo(json.dumps(json.loads(res.model_dump_json()), indent=2))
        return
    t = Table(title=f"cdebt coverage: top {top_n} debt files")
    for c in ("file", "AI lines", "verifiable", "attest-only", "no-quiz", "class", "probe kinds"):
        t.add_column(c)
    for f in res.files:
        t.add_row(
            f.path,
            str(f.ai_lines),
            str(f.verifiable_lines),
            str(f.attest_only_lines),
            str(f.no_quiz_lines),
            f.klass,
            ",".join(f.probe_kinds) or "-",
        )
    console.print(t)

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.0%}"

    console.print(
        f"[bold]Of the AI lines in these files:[/] {pct(res.verifiable_share)} can be VERIFIED by "
        f"a quiz, {pct(res.attest_only_share)} can only be ATTESTED (NON-VERIFIED), "
        f"{pct(res.no_quiz_share)} are in languages with no quiz support. "
        f"Files by class: {res.files_by_class}. SCIP index: {res.scip_index}; sandbox: "
        f"{res.sandbox}."
    )
    for n in res.notes:
        console.print(f"[dim]{n}[/]")


@command("migrate")
def migrate_cmd(
    repo: RepoOpt = Path("."),
    check: Annotated[
        bool, typer.Option("--check", help="exit 1 if .cdebt/understanding.json needs migrating")
    ] = False,
) -> None:
    """Upgrade .cdebt/understanding.json to the current schema (keeps a .bak copy of the old file).
    Reading always migrates in memory; this makes it permanent."""
    from cdebt.store import CURRENT_SCHEMA, Store, StoreError

    r = _repo(repo)
    try:
        st = Store(r)
    except StoreError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    if not st.needs_migration():
        typer.echo(
            f"understanding.json is already schema {CURRENT_SCHEMA} (or absent): nothing to do"
        )
        return
    old = st.migrated_from
    if check:
        typer.echo(
            f"understanding.json is schema {old}; current is {CURRENT_SCHEMA}: run `cdebt migrate`"
        )
        raise typer.Exit(1)
    st.save()
    typer.echo(
        f"migrated understanding.json: schema {old} -> {CURRENT_SCHEMA} "
        f"({len(st.records)} records; backup understanding.v{old}.bak.json)"
    )


@command("mcp")
def mcp_cmd(
    session: Annotated[Path, typer.Option(help="session directory made by `cdebt quiz`")],
    trusted: TrustedOpt = False,
) -> None:
    """Run the cdebt MCP server (stdio) for a coding agent. Started BY the agent, not by hand."""
    _apply_isolation(trusted)  # agent-written harnesses get the same isolation as `cdebt quiz`
    try:
        from cdebt.agents.mcp_server import serve
    except ModuleNotFoundError as e:
        typer.echo(
            f"error: the MCP SDK is not installed ({e.name}); pip install 'cdebt[agent]'", err=True
        )
        raise typer.Exit(2) from e
    serve(session)


hook_app = typer.Typer(no_args_is_help=True, help="Git hook entry points (called by git).")
app.add_typer(hook_app, name="hook")


@hook_app.command("pre-push")
def hook_pre_push(
    remote: Annotated[str, typer.Argument()] = "origin",
    url: Annotated[str, typer.Argument()] = "",
) -> None:
    """Pre-push check: quiz AI-written, not-understood code on your terminal, write notes, push
    them first. Never blocks the push (use CI to block a merge)."""
    from cdebt import hook

    stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
    repo = gitutil.repo_root(Path("."))
    hook.run_pre_push(repo, remote, stdin_text, lambda m: typer.echo(m, err=True))
    raise typer.Exit(0)


@command("init")
def init_cmd(
    repo: RepoOpt = Path("."),
    yes: Annotated[
        bool, typer.Option("--yes", help="consent to every step without prompting")
    ] = False,
    github: Annotated[
        bool,
        typer.Option(
            "--github", help="also require the `cdebt / gate` check on the default branch"
        ),
    ] = False,
    no_hook: Annotated[bool, typer.Option("--no-hook")] = False,
    no_workflow: Annotated[bool, typer.Option("--no-workflow")] = False,
) -> None:
    """Set up the pre-push flow. Every step asks first; nothing is changed without consent."""
    from cdebt import hook
    from cdebt import init as init_mod

    r = _repo(repo)

    def consent(q: str) -> bool:
        return yes or typer.confirm(q, default=False)

    if not no_hook:
        plan = hook.plan_install(r)
        console.print(f"[bold]pre-push hook[/]: {plan.message}")
        if plan.action in ("create", "append") and consent("Apply this change?"):
            hook.apply_install(plan)
            console.print(
                "  done. Skip it any time with `git push --no-verify`; it never blocks a push."
            )
        elif plan.action == "already":
            console.print("  already installed")
    gi = init_mod.check_git_ai()
    console.print(f"[bold]Git AI[/]: {gi}")
    from cdebt.agents import adapters, userconfig

    if userconfig.load() is None and adapters.detect():
        console.print(
            "[bold]coding agent[/]: not connected. Run `cdebt agent connect` to let your own agent "
            "write the quiz questions (optional)."
        )
    pol = init_mod.policy_plan(r)
    console.print(f"[bold]policy[/]: {pol.message}")
    if pol.action == "create" and consent("Write .cdebt/policy.toml?"):
        pol.apply()
    if not no_workflow:
        wf = init_mod.workflow_plan(r)
        console.print(f"[bold]GitHub workflow[/]: {wf.message}")
        if wf.action == "create" and consent("Write the workflow file?"):
            wf.apply()
    if github:
        rs = init_mod.ruleset_plan(r)
        console.print("[bold]branch rule[/] (changes the GitHub repository settings):")
        console.print(rs.message)
        if consent("Create this ruleset on GitHub now?"):
            ok, msg = init_mod.apply_ruleset(rs)
            console.print(f"  {'created' if ok else 'FAILED'}: {msg}")


agent_app = typer.Typer(
    no_args_is_help=True,
    help="Connect your own coding agent so it proposes the quiz questions (opt-in).",
)
app.add_typer(agent_app, name="agent")


@agent_app.command("connect")
def agent_connect(
    name: Annotated[
        str | None, typer.Option(help="claude_code|codex|cursor|command|builtin")
    ] = None,
    command_line: Annotated[
        str | None,
        typer.Option("--command", help="for `command`: your program (gets $CDEBT_MCP_CMD)"),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="skip the confirmation prompt")] = False,
) -> None:
    """Choose the agent. Saved in YOUR user config, never in the repository."""
    from cdebt.agents import adapters, userconfig

    found = adapters.detect()
    console.print(
        "Agent CLIs found: " + (", ".join(f"{k} ({v})" for k, v in found.items()) or "none")
    )
    if name is None:
        options = list(found) + ["command", "builtin"]
        name = typer.prompt(
            f"Which agent should write your quiz questions? {options}", default=options[0]
        )
    if name not in adapters.AGENT_NAMES:
        typer.echo(f"error: unknown agent {name!r}; one of {adapters.AGENT_NAMES}", err=True)
        raise typer.Exit(2)
    if name in ("claude_code", "codex", "cursor") and name not in found:
        typer.echo(f"error: the {name} CLI was not found on this machine", err=True)
        raise typer.Exit(2)
    if name == "command" and not command_line:
        command_line = typer.prompt("Command to run (it receives $CDEBT_MCP_CMD and $CDEBT_PROMPT)")
    console.print(
        "\nWhat the agent will see: only the source and nearby tests of functions that are not yet "
        "verified, through two tools (get_targets, propose_probe). It is told only accepted or "
        "rejected, never an answer. Its other tools are limited to read-only. cdebt runs every "
        "question itself to get the true answer. Your choice is saved in "
        f"{userconfig.path()}."
    )
    if name != "builtin" and not yes and not typer.confirm("Allow this?", default=False):
        raise typer.Exit(1)
    p = userconfig.save(userconfig.AgentChoice(name=name, command=command_line))
    console.print(f"saved agent '{name}' to {p}")


@agent_app.command("status")
def agent_status() -> None:
    """Show the connected agent and which agent CLIs are installed."""
    from cdebt.agents import adapters, userconfig

    c = userconfig.load()
    console.print(f"connected agent: {c.name if c else 'none (builtin generator)'}")
    console.print(f"user config: {userconfig.path()}")
    console.print("detected: " + (", ".join(adapters.detect()) or "none"))
    if c and c.name == "cursor":
        console.print("cursor is UNVERIFIED: register `cdebt mcp` in Cursor's own MCP settings.")


@agent_app.command("disconnect")
def agent_disconnect() -> None:
    """Forget the connected agent (the builtin generator is used again)."""
    from cdebt.agents import userconfig

    console.print("disconnected" if userconfig.clear() else "no agent was connected")


study_app = typer.Typer(
    no_args_is_help=True,
    help="Validity study kit: does familiarity predict answering verified questions? "
    "Team-level only; see docs/STUDY_PROTOCOL.md.",
)
app.add_typer(study_app, name="study")


@study_app.command("init")
def study_init(
    repo: RepoOpt = Path("."),
    out: Annotated[Path, typer.Option(help="study file to send to participants")] = Path(
        "study.json"
    ),
    secret: Annotated[Path, typer.Option(help="coordinator-only grading key")] = Path(
        "study.secret.json"
    ),
    n: Annotated[int, typer.Option(help="number of questions")] = 12,
    seed: int = 0,
    top_files: Annotated[int, typer.Option(help="draw from the N highest-debt files")] = 12,
    kinds: str = "runtime,callgraph,constant",
    trusted: TrustedOpt = False,
) -> None:
    """Build a FIXED question set on a repo. Writes the participant file (no answers) and the
    coordinator secret (HMAC key + answer MACs). Never send the secret file to participants."""
    from cdebt import study as S
    from cdebt.attribution.resolve import default_providers, resolve_file
    from cdebt.mapper import compute_map
    from cdebt.models import AuthorKind
    from cdebt.quiz import build_quiz
    from cdebt.units import unit_for_line

    r = _repo(repo)
    cfg = load_config(r)
    _apply_isolation(trusted)
    res = compute_map(r, cfg, scip_idx=_scip(r, False))
    files = [f.path for f in res.files if f.ai_lines > 0][:top_files]
    ks = tuple(k.strip() for k in kinds.split(",") if k.strip())
    commit = gitutil.resolve(r, "HEAD")
    pool: list[tuple[Any, str]] = []
    with _sandbox_errors():
        for i, rel in enumerate(files):
            probes, units, _ = build_quiz(r, rel, cfg, n=3, seed=seed + i, kinds=ks)
            fa = resolve_file(r, rel, "HEAD", default_providers(r))
            for p in probes:
                u = next((x for x in units if x.symbol == p.symbol), None)
                lines = u.lines() if u else []
                ai = sum(
                    1 for ln in lines if 1 <= ln <= fa.total and fa.kinds[ln - 1] is AuthorKind.AI
                )
                share = ai / len(lines) if lines else None
                pub = S.StudyProbe(
                    id=p.id,
                    kind=p.kind,
                    path=p.path,
                    unit=p.symbol,
                    question=p.question,
                    options=p.options,
                    multi=p.multi,
                    verified_by=p.verified_by,
                    unit_ai_share=share,
                )
                pool.append((pub, S.canonical(p.answer, p.multi)))
    del unit_for_line
    rng = random.Random(seed)
    rng.shuffle(pool)
    chosen: list[tuple[Any, str]] = []
    per_file: dict[str, int] = {}
    for pub, ans in pool:  # at most 2 per file so one file cannot dominate
        if len(chosen) >= n:
            break
        if per_file.get(pub.path, 0) < 2:
            chosen.append((pub, ans))
            per_file[pub.path] = per_file.get(pub.path, 0) + 1
    if len(chosen) < n:
        typer.echo(
            f"warning: only {len(chosen)} questions could be generated (wanted {n})", err=True
        )
    if not chosen:
        typer.echo("error: no questions could be generated", err=True)
        raise typer.Exit(1)
    study, sec = S.build_study(r.name, commit, seed, chosen)
    S.write_json(out, study)
    S.write_json(secret, sec)
    typer.echo(
        f"wrote {out} ({len(chosen)} questions over {len(per_file)} files, commit {commit[:10]}) "
        f"and {secret}. Send ONLY {out} to participants; keep {secret} private."
    )


@study_app.command("run")
def study_run(
    study_file: Path,
    out_dir: Annotated[Path, typer.Option(help="where to write your anonymous response")] = Path(
        "responses"
    ),
    team: Annotated[str | None, typer.Option(help="free-text team label (never a person)")] = None,
    answers: Annotated[
        Path | None, typer.Option(help="scripted JSON {probe_id: {answer, familiarity, consulted}}")
    ] = None,
) -> None:
    """Answer the study questions. Records your answers and, per question, whether you wrote /
    reviewed / never saw the code and whether you consulted anything. No name, no host, no
    timestamps beyond the date. Needs no sandbox (works on Windows)."""
    from cdebt import study as S
    from cdebt.probes.base import LETTERS

    study = S.StudyFile.model_validate_json(study_file.read_text())
    given: dict[str, Any] = json.loads(answers.read_text()) if answers else {}
    resp: dict[str, S.Answer] = {}
    if not answers:
        typer.echo(study.instructions + "\n")
    for i, p in enumerate(study.probes, 1):
        if p.id in given:
            resp[p.id] = S.Answer.model_validate(given[p.id])
            continue
        console.print(
            f"\n[bold]Q{i}/{len(study.probes)}[/] ({p.path} :: {p.unit}) {escape(p.question)}"
        )
        for j, o in enumerate(p.options):
            console.print(f"  {LETTERS[j]}) {escape(o)}")
        a = typer.prompt("answer (letter" + (", comma-separated, or 'none')" if p.multi else ")"))
        f = ""
        while f not in ("w", "r", "n"):
            f = typer.prompt("this code: [w]rote it / [r]eviewed it / [n]ever saw it").lower()[:1]
        c = typer.confirm("did you look anything up or ask anyone (incl. an AI)?", default=False)
        resp[p.id] = S.Answer(
            answer=a,
            familiarity={"w": "wrote", "r": "reviewed", "n": "never"}[f],
            consulted=c,
        )
    rf = S.ResponseFile(
        study_id=study.study_id,
        participant=S.new_participant_id(),
        team=team,
        date=datetime.now(UTC).date().isoformat(),
        answers=resp,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"response-{rf.participant}.json"
    S.write_json(dest, rf)
    typer.echo(f"saved {dest}. Send it to the coordinator. It contains no name or account.")


@study_app.command("analyze")
def study_analyze(
    study_file: Path,
    responses_dir: Path,
    secret: Annotated[Path, typer.Option(help="coordinator secret from `study init`")] = Path(
        "study.secret.json"
    ),
    min_participants: Annotated[int, typer.Option(help="suppress cells below this")] = 5,
    reps: int = 2000,
    seed: int = 0,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Pooled, team-level analysis of all responses. Never prints anything per participant."""
    from cdebt import study as S

    study = S.StudyFile.model_validate_json(study_file.read_text())
    sec = S.SecretFile.model_validate_json(secret.read_text())
    resps = [
        S.ResponseFile.model_validate_json(f.read_text())
        for f in sorted(responses_dir.glob("response-*.json"))
    ]
    try:
        res = S.analyze(study, sec, resps, min_participants=min_participants, reps=reps, seed=seed)
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    if json_out:
        typer.echo(res.model_dump_json(indent=2))
        return
    console.print(
        f"[bold]study {res.study_id}[/]: {res.participants} participants, {res.teams} "
        f"team label(s), {res.responses} responses (cells with < {res.min_participants} "
        "participants are suppressed)"
    )
    t = Table(title="accuracy by self-reported familiarity")
    for c in ("familiarity", "correct/n", "rate", "95% CI (responses, naive)", "participants"):
        t.add_column(c)
    for k, cell in res.by_familiarity.items():
        if cell.suppressed:
            t.add_row(k, "suppressed", "", "", str(cell.participants))
        else:
            t.add_row(
                k,
                f"{cell.correct}/{cell.n}",
                f"{cell.rate:.3f}",
                f"[{cell.ci95[0]:.3f}, {cell.ci95[1]:.3f}]" if cell.ci95 else "",
                str(cell.participants),
            )
    console.print(t)
    console.print(
        "Pre-registered contrast, familiar (wrote|reviewed) minus never saw:", res.familiar_vs_never
    )
    console.print("Same, excluding consulted responses:", res.sensitivity_excluding_consulted)
    for nte in res.notes:
        console.print(f"[yellow]{escape(nte)}[/]")


@study_app.command("power")
def study_power(
    p_familiar: float = 0.70,
    p_never: float = 0.55,
    probes_per_participant: int = 12,
    icc: float = 0.10,
    alpha: float = 0.05,
    power: float = 0.80,
) -> None:
    """Planning estimate of the sample size for the pre-registered contrast."""
    from cdebt import study as S

    deff = S.design_effect(probes_per_participant, icc)
    n = S.required_responses_per_group(p_familiar, p_never, alpha, power, deff)
    typer.echo(
        f"design effect {deff:.2f}; {n} responses per group; about "
        f"{math.ceil(2 * n / probes_per_participant)} participants if every participant answers "
        f"{probes_per_participant} questions split evenly between familiar and never-saw code "
        "(a planning estimate, not a guarantee)"
    )


@command("plan")
def plan_cmd(
    repo: RepoOpt = Path("."),
    rev: str = "HEAD",
    top: int = 20,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    no_scip: Annotated[bool, typer.Option("--no-scip")] = False,
    no_cache: NoCacheOpt = False,
) -> None:
    """Ranked pay-down plan: what to read, review or rewrite first, with effort estimates."""
    from cdebt.plan import compute_plan

    r = _repo(repo)
    _apply_cache(no_cache)
    items = compute_plan(r, load_config(r), rev, top, scip_idx=None if no_scip else _scip(r, False))
    if json_out:
        typer.echo(json.dumps([json.loads(i.model_dump_json()) for i in items], indent=2))
        return
    t = Table(title="cdebt plan", caption="effort = heuristic guess (configurable), not measured")
    for c in ("#", "file", "action", "est. min", "score", "priority", "attribution", "why"):
        t.add_column(c)
    for i in items:
        t.add_row(
            str(i.rank),
            i.path,
            i.action,
            f"{i.effort_min:g}",
            f"{i.score:.2f}",
            f"{i.priority:.3f}",
            i.attribution_source.value,
            i.reason,
        )
    console.print(t)


@command("backtest")
def backtest_cmd(
    data: Annotated[Path, typer.Option(help="dir with AIDev parquet subset")] = Path("data/aidev"),
    fetch: Annotated[
        bool, typer.Option("--fetch", help="download needed columns (network)")
    ] = False,
    out: Path = Path("docs/RESULTS.md"),
    seed: int = 0,
    reps: int = 2000,
    revision: str = "main",
    matched: Annotated[
        bool, typer.Option("--matched", help="Analysis 2: within-repo matched pairs")
    ] = False,
) -> None:
    """Does the (proxy) debt score predict later fixes and reverts on AIDev? Reproducible."""
    try:
        from cdebt.backtest import aidev, analysis, report
    except ImportError:
        typer.echo("error: backtest needs extras: pip install 'cdebt[backtest]'", err=True)
        raise typer.Exit(2) from None

    if fetch:
        console.print(f"fetching AIDev@{revision} columns into {data} ...")
        aidev.fetch(data, revision)
    if not (data / "pull_request.parquet").exists():
        typer.echo(f"error: no data in {data}; run with --fetch", err=True)
        raise typer.Exit(2)
    mf = data / "manifest.json"
    manifest = json.loads(mf.read_text()) if mf.exists() else None
    units = aidev.build_units(aidev.load_tables(data))
    if matched:
        from cdebt.backtest import matched as m2

        mout = out if out.name != "RESULTS.md" else out.with_name("RESULTS_MATCHED.md")
        mres = m2.run(units, reps=reps, seed=seed)
        mcmd = f"cdebt backtest --matched --data {data} --out {mout} --seed {seed} --reps {reps}"
        mout.parent.mkdir(parents=True, exist_ok=True)
        mout.write_text(report.render_matched(mres, manifest, mcmd))
        mout.with_suffix(".json").write_text(json.dumps(mres, indent=2, default=float))
        console.print(f"wrote {mout} and {mout.with_suffix('.json')}")
        return
    res = analysis.run(units, reps=reps, seed=seed)
    cmd = f"cdebt backtest --data {data} --out {out} --seed {seed} --reps {reps}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.render(res, manifest, cmd))
    out.with_suffix(".json").write_text(json.dumps(res, indent=2, default=float))
    console.print(f"wrote {out} and {out.with_suffix('.json')}")


if __name__ == "__main__":
    app()
