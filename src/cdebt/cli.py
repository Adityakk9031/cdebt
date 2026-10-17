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


if __name__ == "__main__":
    app()
