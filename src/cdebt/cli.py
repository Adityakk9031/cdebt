"""cdebt command line. Measures code, never people."""

from __future__ import annotations

import contextlib
import functools
import json
from collections.abc import Callable, Iterator
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
) -> None:
    """Which files hold the most AI-written code nobody has verifiably understood?"""
    r = _repo(repo)
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


@command("quiz")
def quiz_cmd(
    file: Path,
    repo: RepoOpt = Path("."),
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
) -> None:
    """Ask questions whose answers come from EXECUTING the code, then record understanding."""
    from cdebt.models import Probe
    from cdebt.probes.base import LETTERS
    from cdebt.quiz import build_quiz, dirty, record_results

    r = _repo(repo)
    cfg = load_config(r)
    rel = _rel(file, r)
    ks = tuple(k.strip() for k in kinds.split(",") if k.strip())
    _apply_isolation(trusted)
    with _sandbox_errors():
        probes, units, skips = build_quiz(r, rel, cfg, n=n, seed=seed, kinds=ks)
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
    json_out: Annotated[bool, typer.Option("--json")] = False,
    markdown: Annotated[Path | None, typer.Option(help="write PR-comment markdown")] = None,
) -> None:
    """CI check: fail when AI-authored changed lines sit in files nobody has understood."""
    from cdebt.gate import run_gate, to_markdown

    r = _repo(repo)
    try:
        res = run_gate(
            r,
            load_config(r),
            base,
            head,
            max_ai_lines=max_ai_lines,
            strict_unknown=strict_unknown,
            require=require or "default",
        )
    except ValueError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    except gitutil.GitError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from None
    if markdown:
        markdown.write_text(to_markdown(res))
    if json_out:
        typer.echo(json.dumps(json.loads(res.model_dump_json()), indent=2))
    else:
        console.print(
            f"[bold]cdebt gate: {'PASS' if res.passed else 'FAIL'}[/] "
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
    raise typer.Exit(0 if res.passed else 1)


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
) -> None:
    """What fraction of the top-N debt files can be VERIFIED, only ATTESTED, or not quizzed at
    all (incl. TypeScript)? Run this before trusting the gate."""
    from cdebt.coverage import compute_coverage

    r = _repo(repo)
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


@command("plan")
def plan_cmd(
    repo: RepoOpt = Path("."),
    rev: str = "HEAD",
    top: int = 20,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    no_scip: Annotated[bool, typer.Option("--no-scip")] = False,
) -> None:
    """Ranked pay-down plan: what to read, review or rewrite first, with effort estimates."""
    from cdebt.plan import compute_plan

    r = _repo(repo)
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
