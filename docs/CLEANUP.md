# Cleanup: things created while building cdebt that live outside the repo

**Done on 2026-10-22 at the author's request:** the Docker stack (Colima VM and disk, Docker and
Lima via Homebrew, `~/.colima`; section 3), the temporary clones, logs, scripts and the
self-hosted runner download in `/private/tmp`, and the test PRs 3, 4 and 5 (closed) with their
three test branches in `Adityakk9031/cdebt` (deleted). **Kept on purpose:** Git AI (`~/.git-ai`),
the Claude Code CLI and its login (needed for the demo; it belongs to the Claude app, not to this
project), the other scratch branches of the earlier PRs 1 and 2, and both scratch GitHub repos
(deleting a repo is irreversible; commands in section 1). Still manual: the `cubic-dev-ai` app
(section 2). Docker-dependent tests now skip until Docker is installed again.

Nothing below has been run for you. Each block is independent; run what you want.

## 1. The two scratch GitHub repos (private)

`cdebt-action-scratch` (the Action test PR) and `cdebt-ci-scratch` (a full copy of the cdebt
source used to run CI on real runners). `gh` needs the `delete_repo` scope first:

```bash
gh auth refresh -h github.com -s delete_repo
gh repo delete Adityakk9031/cdebt-action-scratch --yes
gh repo delete Adityakk9031/cdebt-ci-scratch --yes
```

(The `origin` remote that pointed at `cdebt-ci-scratch` has already been removed from your local
cdebt repo; the local repo has no remotes now.)

## 2. The `cubic-dev-ai` GitHub App

It reviewed the scratch PR, so it is installed on your account (or an org you own). GitHub has
no API for a user to uninstall an app, so this is a web step:

1. Open <https://github.com/settings/installations> (for an org:
   `https://github.com/organizations/<ORG>/settings/installations`).
2. Find **cubic** and click **Configure**.
3. Under "Danger zone" click **Uninstall**.

(Before uninstalling you can check which repos it can see on that same Configure page.)

## 3. Docker, Colima and the pulled image

Installed via Homebrew: `colima`, `docker`, `lima` (plus their dependencies). The VM's Docker
context became your default context. Run in this order:

```bash
docker rmi python:3.11-slim            # the pulled image (lives inside the Colima VM)
docker context use default             # point the docker CLI away from Colima
colima stop
colima delete --force                  # deletes the VM and its disk (~/.colima/default)
docker context rm colima               # remove the leftover context entry
brew uninstall colima docker lima
rm -rf ~/.colima ~/.lima
```

If you would rather keep Docker but drop only cdebt's traces: `docker rmi python:3.11-slim`
is the only cdebt-specific item (cdebt names its containers `cdebt-<pid>-<time>` and removes
them itself; `docker ps -a --filter name=cdebt-` should be empty).

## 4. Git AI (binary, daemon and data)

Installed by hand to `~/.git-ai/bin/git-ai` (not through `install.sh`; no shell, git or agent
configuration was changed). Its background daemon may still be running:

```bash
pkill -f git-ai                        # stop the daemon if it is running
rm -rf ~/.git-ai                       # binary, daemon socket, local databases
```

## 5. Temporary files

```bash
rm -rf /private/tmp/cdebt-pilot /private/tmp/cdebt-pilot-attested /private/tmp/claude-501/gp
rm -f /private/tmp/sandbox_old.py /private/tmp/sandbox_new.py /private/tmp/stress_repro.py \
      /private/tmp/cov.py /private/tmp/pilot.log /private/tmp/pilot_attest.log /private/tmp/cands.txt
```

The pilot clones in `/private/tmp/cdebt-pilot` are shallow-free full clones of three public
repos (space_chassis, hive, agent-meta) plus `.cdebt/` caches (including a 16 MB SCIP index).

## 6. Optional

```bash
uv python uninstall 3.11               # only if you do not use uv's Python 3.11 elsewhere
rm -rf $HOME/cdebt/.venv $HOME/cdebt/dist $HOME/cdebt/data
```

`data/aidev` holds the downloaded AIDev column subset (about 80 MB); `cdebt backtest --fetch`
recreates it.
