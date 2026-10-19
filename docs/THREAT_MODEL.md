# Threat model

Scope: what an attacker can do through cdebt, what the defences are, what was tested, and what is
**not** defended. Read with `docs/LIMITS.md`. Last reviewed for v1.0.0-rc1.

## 1. What cdebt does that is security-relevant

| action | commands | runs untrusted code? |
|---|---|---|
| reads git history, notes, working files | all | no (read-only `git` plumbing; parsing only) |
| parses source with tree-sitter | map, quiz, coverage, gate, study | no (parsing untrusted text) |
| **executes functions / imports modules of the repo being analysed** | quiz, coverage, attest, study | **yes: probes** |
| **runs the repo's own test suite on mutated code** | quiz `--kinds mutation` | **yes: arbitrary project code** |
| runs SCIP indexers (`npx @sourcegraph/scip-python`, `scip-typescript`) | index | runs npm packages, analyses the repo (does not execute it); **not sandboxed** |
| reads `.cdebt/understanding.json` from the PR head | gate | no (data; attacker-controllable) |
| downloads the AIDev dataset | backtest `--fetch` | no (data only) |

## 2. Adversaries

- **A1: accidental harm.** AI-written or just buggy code that writes files, opens sockets, loops
  forever or allocates everything when imported or called. The most likely case; the main reason
  the sandbox exists.
- **A2: malicious PR author / repo owner.** Someone who wants the machine of whoever runs
  `cdebt quiz` (a maintainer, a CI runner with secrets) to run their code, read `~/.ssh`, `~/.aws`,
  environment secrets, other repos, or to exfiltrate over the network. Realistic for CI on pull
  requests from forks.
- **A3: malicious or compromised dependency / tool** (npm package fetched by `npx`, a PyPI
  dependency of cdebt). Not defended beyond pinning advice.
- **A4: forged evidence.** Someone edits `.cdebt/understanding.json` or attribution to pass the gate.
  This is an integrity attack on the *signal*, not on the machine (see 6).

Out of scope: a determined attacker with native-code or kernel exploits, side channels, a
compromised OS or Docker daemon, physical access.

## 3. Trust decisions

- A repository is **untrusted by default**. `--trusted` or `CDEBT_TRUSTED=1` (exactly `1`), set by
  **the person running cdebt**, switches to the host sandbox. Trust is **never** read from a file
  in the repository, `.cdebt/config.toml` or `pyproject.toml`, because those are attacker-controlled
  in A2 (tested: `tests/test_trust_policy.py::test_repo_config_cannot_mark_itself_trusted`).
- Untrusted ("strong") means: Docker (image already pulled, `--network none`, no capabilities,
  unprivileged user, memory/pids limits) **or**, on macOS, a strict Seatbelt profile (below).
  If neither exists cdebt **refuses to execute anything** and says why (fail closed).
- Mutation probes run the project's own test suite (needs the project's dependencies), so Docker
  cannot be used for them. They are skipped for untrusted repos unless strict Seatbelt is available
  (they still need `--trusted` on Linux without it).

## 4. Defence layers (probe execution)

1. **Static screen** (tree-sitter / `ast`): functions and modules that import or call
   side-effecting things (filesystem, network, processes, time, randomness, `eval`, globals) are
   skipped, never run. Reduces accidents (A1); it is a filter, not a boundary.
2. **Isolated interpreter**: Python `-I` in a fresh temp dir, scrubbed environment (no secrets,
   `HOME` = sandbox root), Node `--permission` with fs access limited to the sandbox root.
3. **In-process guards**: Python audit hook blocks file writes (anywhere, even in the root), reads
   outside the sandbox root and the interpreter's own directories, directory listing outside it,
   sockets, `ctypes`, process creation, **`os.kill`/`killpg`/`pthread_kill`** and
   **`sqlite3.connect` of a real file** (C code opens the file behind Python's audited `open`).
   Node: network modules and `fetch` disabled, `process.kill/abort/binding` replaced, plus the
   permission model (no fs writes, no child processes, no workers).
4. **Resource limits**: wall-clock deadline on its own timer, CPU/NOFILE/FSIZE rlimits, RSS watchdog,
   output cap (8 MB), descendant-process cap (64), process-tree kill (descendants first, then the
   group), refusal to signal a reused PID, bounded Docker cleanup, orphan-container reaper.
5. **OS boundary**: Docker (above), or macOS Seatbelt: no network, writes only under the root,
   **cannot signal processes outside its sandbox**, and in strict mode **cannot read `$HOME`**
   (except the interpreter prefixes and the root) and cannot fork (probe runners).

Layers 2-4 are best-effort *inside* the process being contained. Only layer 5 (and the Node
permission model for fs/child processes) is enforced by something the payload cannot rewrite.

## 5. What the fuzzing found (and fixed)

`tests/test_sandbox_fuzz.py` and `test_sandbox_node_fuzz.py` compose random hostile payloads from
seven families and check invariants (outside files unchanged, no new files, no connections, a
sentinel process still alive, no secret in any output, bounded time). Each payload runs in a
disposable worker so one that kills its parent cannot take down the harness. Findings, all fixed
and now covered:

| finding | impact | fix |
|---|---|---|
| `os.kill(os.getppid(), 9)` was not blocked | a payload could kill cdebt, and `os.kill(-1, 9)` every process of the user | audit hook blocks `os.kill`/`killpg`/`pthread_kill`; Seatbelt `(deny signal)` except self/same sandbox |
| Seatbelt profile allowed signalling other processes | `/bin/kill` from inside the sandbox killed an outside process | profile now denies signals to other sandboxes |
| `sqlite3.connect(path)` creates/opens files in C | file write/read outside root bypassing the audited `open()` | `sqlite3.connect` of anything but `:memory:` denied |
| Python reads were unrestricted | a payload could read `~/.ssh`, other repos, `/proc/*/environ` and return them in a probe answer | read allowlist (root, interpreter prefixes, `/usr`, system libs); strict Seatbelt hides `$HOME` |
| unbounded stdout/stderr read by the parent | memory exhaustion of cdebt itself | capped readers; the run is killed when a stream goes over |
| Node's permission model does **not** block signals or the network | `process.kill(pid)` killed an outside process | runner replaces `process.kill/abort/binding`; network entry points and `fetch` removed; OS layer blocks the rest |

Fuzz budget: 7 Python families + 1 Node family, 6 iterations each by default (every PR run);
a 20-iteration soak with a second seed also passed. `CDEBT_FUZZ_N` and `CDEBT_FUZZ_SEED` scale it.
A passing fuzz run is evidence, not proof.

### 5b. Second pass: a fresh red-team round (v1.0.0-rc2)

`tests/test_sandbox_redteam.py` adds explicit payload classes the first suite lacked. Run against
the *previous* sandbox, **6 of 13 guard-tampering variants disabled the in-process guard** and a
repository symlink got a host file copied into the sandbox; all are fixed and now pass.

| class | result on the old code | fix |
|---|---|---|
| Tampering with the guard: swapping the hook's `__code__` via `__main__`, via `gc.get_objects`, via traceback frame walking, via `sys._getframe`; patching `os.path.realpath`; patching builtins (`any`, `isinstance`, `str`, `len`) | **defeated the guard** (gc, `__main__`, builtins, realpath, both frame walks) | the guard now lives in closures created inside a function that has returned (no module global, no reachable frame), binds every builtin it uses as a default argument, uses its own `realpath` built on captured `lstat`/`readlink`, and `gc.get_objects/get_referrers/get_referents`, `sys._getframe`, `sys._current_frames`, `settrace`, `setprofile` and `sys.monitoring` raise |
| A repository symlink to a host file (`leak -> ~/.ssh/id_rsa`): `copy_tree` followed it and **copied the secret into the sandbox root**, where a probe could read and return it | **leaked** | `copy_tree` never copies through a symlink; in-repo links are kept as relative links, others dropped |
| Symlinks planted inside the root: Node's permission model **follows them out of the root** | **leaked** (Node) | the root is scrubbed before every run (`sandbox.sanitize_root`): symlinks resolving outside it and regular files with more than one hard link are removed; the Python guard also refuses reads of multiply-linked files |
| Hard links inside the root aliasing an outside file | path guards cannot see them | scrub above; Python guard checks `st_nlink` |
| Shared memory (`multiprocessing.shared_memory`, `_posixshmem`) | not demonstrable on macOS | imports blocked (Linux behaviour is checked in CI, not locally) |
| `/proc` and `/sys` reads (`environ`, `mem`, `maps`, `cmdline`, `root/..`, `net/tcp`, DMI ids), `/etc/{passwd,shadow,hosts,resolv.conf}`, `/dev/shm` | not readable (macOS has no `/proc`; Linux covered by CI) | `/proc/self` removed from the read allowlist |
| Credential files in the real home (`.aws`, `.ssh`, `gh`, `.netrc`, docker, npm, git, pypirc), found via `pwd.getpwuid` | not readable | (held) read allowlist and strict Seatbelt |
| Unix sockets including agent-style (`SSH_AUTH_SOCK`, `docker.sock`), `socketpair` | blocked | (held) |
| Signal tricks (SIGSTOP of self, ignoring SIGTERM/SIGINT, itimers, `sigtimedwait`, `pause`) | none evaded the deadline | (held) |
| Decompression and allocation bombs (zlib, gzip, bz2, lzma, zip in memory, 2^40-byte allocations, huge `mmap`) | contained | (held) |
| Node: `inspector.open` (debugger port), V8 flags, diagnostic reports, WebAssembly memory | not shown to fail; added as precautions | `inspector.*`, `v8.setFlagsFromString/writeHeapSnapshot`, `process.report.writeReport` blocked, `WebAssembly` removed |
| Forging the probe result with a raw `os.write(1, ...)` | works | **accepted**: the repo already controls what its own functions return |

Not shown by this pass: anything about hostile native code, kernel or Docker flaws, or timing
side channels. Passing these tests is evidence, not proof.

## 6. Residual risks (not defended)

- **The in-process Python guard is still best-effort.** It resisted every tampering variant in the
  second pass, but it is Python code running in the same process as the attacker's Python; the real
  boundaries are Seatbelt and Docker.
- **Hostile native code / interpreter exploits** (ctypes is blocked by a hook that native code
  can bypass; V8 or CPython memory-safety bugs). Docker narrows the damage; a VM would be better.
- **Host mode is not a boundary against A2.** `--trusted` on a hostile repo is the user's mistake.
  On Linux without Docker the host sandbox has no OS-level layer at all (no Seatbelt equivalent is
  implemented; `bubblewrap`/`unshare` are not used): it relies on the audit hook and rlimits.
- **Docker is only as good as the daemon.** Containers run unprivileged without capabilities, but
  a Docker escape is out of scope. Probes inside Docker run with the *image's* Python/Node, not
  the project's dependencies (lower coverage; documented).
- **Test suites (mutation probes)** run with only rlimits + Seatbelt (macOS) or nothing OS-level
  (Linux host). They need `--trusted` for that reason.
- **Indexers are not sandboxed**: `index` runs npm-distributed tools on the repository. Treat
  `cdebt index` like running `npx` on untrusted code, or run it in a container.
- **Process-tree kill cannot see re-parented grandchildren** (no cgroups on macOS); they remain
  bound by their inherited CPU rlimit.
- **Forged evidence (A4).** `.cdebt/understanding.json` is committed by whoever opens the PR, so
  the gate can be passed by editing it, and attested records need no proof at all. Mitigate with
  CODEOWNERS on `.cdebt/` and branch protection; treat the gate as visibility, not enforcement.
- **Attribution can be forged** (commit trailers, notes) the same way.
- **Supply chain of cdebt itself**: dependencies are not pinned by hash; no SBOM or signed releases yet.
- **Resource exhaustion of the host by many sandboxes in parallel** (no global concurrency cap).
- **Path/permission model differences**: Windows is unsupported for execution (see `docs/LIMITS.md`).

## 7. Operating guidance

- On CI for pull requests from forks: do not pass `--trusted`; use a runner with Docker and pull
  `python:3.11-slim` / `node:24-slim` first; give the job no secrets; run `gate` (read-only) in
  the privileged workflow and `quiz`/`coverage` only in an unprivileged one.
- On a laptop: macOS gets strict Seatbelt automatically; Linux needs Docker.
- Report a sandbox escape privately to the maintainers; include the payload (the fuzz harness
  `tests/_fuzz_worker.py` reproduces a payload in a disposable process).
