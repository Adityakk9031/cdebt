"""Pick attribution per line by priority git-ai > agent-trace > trailer > none, and label it."""

from __future__ import annotations

from pathlib import Path

from cdebt import gitutil
from cdebt.attribution.agent_trace import AgentTraceProvider
from cdebt.attribution.base import Provider
from cdebt.attribution.gitai import GitAiProvider
from cdebt.attribution.trailer import TrailerProvider
from cdebt.cache import Cache, make_key
from cdebt.cache import enabled as cache_enabled
from cdebt.models import AttributionSource, AuthorKind, FileAttribution


def default_providers(repo: Path, *, use_trailers: bool = True) -> list[Provider]:
    ps: list[Provider] = [GitAiProvider(repo), AgentTraceProvider(repo)]
    if use_trailers:
        ps.append(TrailerProvider(repo))
    return ps


_KIND = {AuthorKind.AI: "a", AuthorKind.HUMAN: "h", AuthorKind.UNKNOWN: "u"}
_SRC = {
    AttributionSource.GIT_AI: "g",
    AttributionSource.AGENT_TRACE: "t",
    AttributionSource.TRAILER: "r",
    AttributionSource.NONE: "n",
}


def _encode(fa: FileAttribution) -> dict[str, object]:
    return {
        "k": "".join(_KIND[k] for k in fa.kinds),
        "s": "".join(_SRC[s] for s in fa.sources),
        "c": fa.coarse,
    }


def _decode(path: str, d: dict[str, object]) -> FileAttribution:
    kinds = {v: k for k, v in _KIND.items()}
    srcs = {v: k for k, v in _SRC.items()}
    return FileAttribution(
        path=path,
        kinds=[kinds[c] for c in str(d["k"])],
        sources=[srcs[c] for c in str(d["s"])],
        coarse=bool(d["c"]),
    )


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("cdebt")
    except PackageNotFoundError:  # pragma: no cover
        return "dev"


def resolve_file(
    repo: Path, path: str, rev: str = "HEAD", providers: list[Provider] | None = None
) -> FileAttribution:
    """Per-line attribution of `path` at `rev`. Cached on disk, keyed by commit sha + the file's
    blob sha + the providers' inputs (notes ref, trailer patterns, agent-trace files) + the cdebt
    version, so any change to any of them is a miss."""
    providers = providers if providers is not None else default_providers(repo)
    cache = Cache(repo)
    key: str | None = None
    if cache_enabled():
        try:
            sha = gitutil.rev_sha(repo, rev)
            blob = gitutil.blob_shas(repo, sha).get(path, "absent")
            sig = "|".join(f"{p.source.value}={p.cache_key()}" for p in providers)
            key = make_key("attribution", _version(), sha, path, blob, sig)
            hit = cache.get("attribution", key)
            if hit is not None:
                return _decode(path, hit)
        except gitutil.GitError:
            key = None
    fa = _resolve_uncached(repo, path, rev, providers)
    if key is not None:
        cache.put("attribution", key, _encode(fa))
    return fa


def _resolve_uncached(
    repo: Path, path: str, rev: str, providers: list[Provider]
) -> FileAttribution:
    try:
        bl = gitutil.blame(repo, path, rev)
    except gitutil.GitError:
        return FileAttribution(path=path, kinds=[], sources=[])
    n = len(bl)
    kinds = [AuthorKind.UNKNOWN] * n
    sources = [AttributionSource.NONE] * n
    coarse = False
    for prov in providers:  # priority order: first claim per line wins
        for i, c in enumerate(prov.claims(path, rev, bl)):
            if c is not None and kinds[i] is AuthorKind.UNKNOWN:
                kinds[i], sources[i] = c[0], prov.source
                coarse = coarse or c[1]
    return FileAttribution(path=path, kinds=kinds, sources=sources, coarse=coarse)


def resolve_files(
    repo: Path, paths: list[str], rev: str = "HEAD", providers: list[Provider] | None = None
) -> dict[str, FileAttribution]:
    providers = providers if providers is not None else default_providers(repo)
    return {p: resolve_file(repo, p, rev, providers) for p in paths}
