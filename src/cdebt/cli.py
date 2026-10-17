"""cdebt command line. Measures code, never people."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cdebt import gitutil
from cdebt.config import load_config
from cdebt.mapper import MapResult, compute_map
from cdebt.scip import index_path, load_index

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Measure, test and pay down comprehension debt. Measures code, not people.",
)
console = Console()


@app.callback()
def _main() -> None:
    """cdebt: comprehension-debt toolkit."""


RepoOpt = Annotated[Path, typer.Option("--repo", help="Repository path.")]


def _repo(p: Path) -> Path:
    try:
        return gitutil.repo_root(p)
    except gitutil.GitError:
        typer.echo(f"error: {p} is not a git repository", err=True)
        raise typer.Exit(2) from None


def _scip(repo: Path, build: bool) -> object | None:
    from cdebt.scip import build_index

    ip = index_path(repo)
    if build and not ip.exists():
        build_index(repo)
    return load_index(ip) if ip.exists() else None


def render_map(res: MapResult, top: int) -> None:
    s = res.summary
    t = Table(
        title=f"cdebt map @ {s.rev}",
        caption="score = ai_share x (1-understood) x "
        "(1+w*churn) x blast. `unknown` attribution is NOT human-written.",
    )
    for c, j in (
        ("file", "left"),
        ("AI%", "right"),
        ("understood%", "right"),
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
            f"{f.understood_share:.0%}" if f.ai_lines else "-",
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
    lo, hi = s.understood_units_ci95
    console.print(
        f"[bold]Summary[/] files={s.files} lines={s.total_lines} | AI={s.ai_share:.1%} "
        f"({s.ai_lines}) unknown={s.unknown_share:.1%} ({s.unknown_lines}) | "
        f"AI lines understood: {und} | AI units understood: {s.units_understood}/"
        f"{s.units_with_ai} (95% CI {lo:.0%}-{hi:.0%}) | attribution sources: {srcs}"
    )
    if s.coarse_attribution:
        console.print("[yellow]* coarse: commit-level trailer heuristic, not line-level.[/]")


@app.command("map")
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
    res = compute_map(r, load_config(r), rev, scip_idx=idx)  # type: ignore[arg-type]
    if json_out:
        typer.echo(json.dumps(json.loads(res.model_dump_json()), indent=2))
    else:
        render_map(res, top)


@app.command("index")
def index_cmd(repo: RepoOpt = Path("."), language: str = "python") -> None:
    """Build the SCIP index (needs node/npx; writes only .cdebt/cache/)."""
    from cdebt.scip import build_index

    r = _repo(repo)
    out = build_index(r, language)
    if out is None:
        typer.echo("error: indexing failed (need node/npx and network for the indexer)", err=True)
        raise typer.Exit(2)
    typer.echo(f"wrote {out}")


@app.command("quiz")
def quiz_cmd(
    file: Path,
    repo: RepoOpt = Path("."),
    n: int = 5,
    seed: int = 0,
    kinds: Annotated[
        str, typer.Option(help="comma list: runtime,callgraph,mutation")
    ] = "runtime,callgraph",
    label: Annotated[
        str | None, typer.Option(help="audit label, never reported per person")
    ] = None,
    answers: Annotated[Path | None, typer.Option(help="JSON {probe_id: answer}")] = None,
    emit: Annotated[Path | None, typer.Option(help="write questions JSON and exit")] = None,
    expires_days: Annotated[int | None, typer.Option()] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="list every skipped unit")] = False,
) -> None:
    """Ask questions whose answers come from EXECUTING the code, then record understanding."""
    from cdebt.models import Probe
    from cdebt.probes.base import LETTERS
    from cdebt.quiz import build_quiz, dirty, record_results

    r = _repo(repo)
    cfg = load_config(r)
    rel = str((file if file.is_absolute() else Path.cwd() / file).resolve().relative_to(r))
    ks = tuple(k.strip() for k in kinds.split(",") if k.strip())
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
    given: dict[str, str] = json.loads(answers.read_text()) if answers else {}
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


@app.command("gate")
def gate_cmd(
    repo: RepoOpt = Path("."),
    base: str = "origin/main",
    head: str = "HEAD",
    max_ai_lines: int = 0,
    strict_unknown: Annotated[bool, typer.Option("--strict-unknown")] = False,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    markdown: Annotated[Path | None, typer.Option(help="write PR-comment markdown")] = None,
) -> None:
    """CI check: fail when AI-authored changed lines sit in files nobody has understood."""
    from cdebt.gate import run_gate, to_markdown

    r = _repo(repo)
    try:
        res = run_gate(
            r, load_config(r), base, head, max_ai_lines=max_ai_lines, strict_unknown=strict_unknown
        )
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
        for n in res.notes:
            console.print(f"[yellow]{n}[/]")
    raise typer.Exit(0 if res.passed else 1)


@app.command("plan")
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
    items = compute_plan(r, load_config(r), rev, top, scip_idx=None if no_scip else _scip(r, False))  # type: ignore[arg-type]
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


@app.command("backtest")
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
