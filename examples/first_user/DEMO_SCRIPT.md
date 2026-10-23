# First-user demo: record script (about 7 minutes)

You play a developer who has never used cdebt. Everything below was rehearsed end to end on
2026-10-22 (fresh install from the built wheel, real Git AI notes, a real `git push` through the
hook, the real Claude Code agent). Record with **Cmd+Shift+5 -> Record Selected Portion** so only the
terminal is captured. Use a large font and a short prompt (`PS1='$ '`).

## Before you press record (not on camera)

```bash
# 1. a clean place and a clean environment
python3 -m venv ~/cdebt-venv && source ~/cdebt-venv/bin/activate
# 2. have these ready: Git AI installed (~/.git-ai/bin/git-ai) and the Claude Code CLI logged in
# 3. plug in the Mac, keep the lid open, close other windows and notifications
# 4. build the demo project (a stand-in for "my AI agent just wrote this")
DEMO_FORCE_FOLLOWUP=1 bash examples/first_user/setup_demo.sh     # path: ~/cdebt-demo
```

`DEMO_FORCE_FOLLOWUP=1` stages the timed follow-up so it reliably appears on camera. Normally it
only appears when a hard question is answered no slower than the easy ones. Say that on camera.
To start over: `rm -rf ~/cdebt-demo ~/cdebt-demo-remote.git` and run the setup again.

## On camera

| time | what you type | what you say |
|---|---|---|
| 0:00 | *(title card or just the terminal)* | "AI writes more and more of our code. Nobody knows how much of it a human has actually understood. cdebt makes that visible. It measures code and teams, never individuals." |
| 0:20 | `pip install --pre "cdebt[agent]"` then `cdebt --version` | "This is how a new user installs it. The agent extra is only needed if you want your own coding agent to write the questions." *(If PyPI is not ready: `pip install "cdebt[agent] @ git+https://github.com/Adityakk9031/cdebt"`.)* |
| 0:40 | `cd ~/cdebt-demo && git log --oneline` | "A tiny billing project. The last commit was written by an AI agent, and Git AI recorded exactly which lines." |
| 0:55 | `cdebt init` *(answer `y` to each step)* | "init asks before every change: a pre-push hook that never overwrites an existing one, a policy file, a GitHub workflow." |
| 1:20 | `cdebt agent connect` *(choose claude_code, answer `y`)* | "Optional: my own coding agent writes the questions. It only sees the code being quizzed, is never told an answer, and cdebt runs every question itself to get the true answer." |
| 1:50 | `cdebt map` | "Where is the AI-written code nobody has understood? Verified: zero percent. Note the attribution source says git-ai, line-level." |
| 2:15 | `cdebt coverage` | "Before trusting anything: how much of this can a quiz actually verify? On real code this is often small, and I will say so in the README." |
| 2:35 | `git push -u origin feature` | "Now I push. The hook sees AI-written code nobody has understood and quizzes me, on my own terminal, before the push." |
| 2:45 | *answer the questions* (see below) | "Every answer is checked by running the code in a sandbox. No AI decides whether I am right." |
| 3:30 | *(get one wrong on purpose, early)* | "I got that one wrong. One slip is tolerated: with 4 questions I need 3 right, and a wrong answer never adds a harder question." |
| 3:50 | *(the timed follow-up appears)* | "A short timed follow-up with a visible countdown, answered by tracing the real execution. I am staging this so it shows; normally it only triggers when the answers all come at a suspiciously even pace. Nothing about my timing is stored." |
| 4:20 | *(push finishes: "notes pushed before the branch")* | "The proof of understanding travels with the push, as a git note, bound to a hash of this exact code." |
| 4:35 | `git notes --ref=cdebt show HEAD \| python3 -m json.tool \| head -45` | "This is the record cdebt just made: the question, the true answer, **how it was verified**, and the code hash it is bound to. Notice it even keeps my wrong first answer. There is no timing in it." |
| 5:05 | *(second terminal)* `bash ~/cdebt-public/examples/first_user/watch_sandbox.sh`, then in the first terminal `cdebt quiz pkg/pricing.py`, then `python ~/cdebt-public/examples/first_user/sandbox_peek.py` | "Behind the scenes. **Part 1:** my real function runs in a throw-away sandbox, twice, and cdebt records which lines ran. **Part 2:** the same sandbox is asked to read a secret, overwrite a file, connect to the network, kill a process, run a shell command. Every one is blocked, and I check from outside that nothing happened." |
| 6:10 | `cdebt gate --base origin/main` | "The CI check. All AI lines are understood." |
| 6:25 | edit `late_fee` (change `* 2` to `* 3`), commit, `cdebt gate --base origin/main` | "Understanding expires the moment the code changes. The gate flags this function again." |
| 6:45 | `cdebt gate --base origin/main --enforce; echo $?` | "Advisory by default. With --enforce it exits 1 and blocks the merge." |
| 6:55 | *(close, see "Honest close")* | |

### How cdebt really runs someone else's function (say this, then show it)

This is the product's own code path, not a demo. When `cdebt quiz FILE` (or the pre-push hook)
needs the true answer to "what does `late_fee(20, 14)` return?":

1. It reads your file and finds the function, then checks it for side effects it cannot make safe
   (files, network, time, randomness). Those are skipped, not run.
2. It copies the project into a **fresh temp folder** and starts a **brand-new child process**:
   `python -I runner.py` (isolated mode, a scrubbed environment with `HOME` pointing at the temp
   folder). On a Mac that child runs under the **strict macOS sandbox** (`sandbox-exec`: no network,
   no writes outside the temp folder, your home folder hidden, no signalling other processes). On
   Linux it runs in **Docker** with `--network none`. If neither exists, cdebt refuses to run
   untrusted code. There are also CPU, memory, file-size and time limits.
3. Inside that child, **before your code is imported**, a guard is installed that raises an error on
   file writes, network, subprocesses and similar. The child imports your module and calls the
   function twice. If the two results differ, the question is thrown away (flaky). Otherwise the
   result is the **answer key**.
4. The parent reads that result as JSON and compares **your answer to it by a plain rule**. For "did
   line N run?" questions the child traces the real execution instead.

Code: `src/cdebt/sandbox.py` (`run_in_sandbox`, the runner, the macOS profile and Docker launch) and
`src/cdebt/probes/runtime.py` (the question generator that calls it).

**Show it live (second terminal).** In a second window run
`bash ~/cdebt-public/examples/first_user/watch_sandbox.sh`, then in the first window run
`cdebt quiz pkg/pricing.py`. The watcher prints one line per sandbox child the OS starts, each
`python -I .../cdebt-runner-XXXX/runner.py` with a new process ID and its own temp folder, for example
seven of them for one run on the demo project. Say: "every one of those is cdebt running a
function from this repository in its own sandbox."

**`sandbox_peek.py` is the same code, aimed by hand.** It is not a separate or fake sandbox: it calls
the product's own `cdebt.sandbox.run_code` and the same probe executor `cdebt quiz` uses, only so
that part 2 can push hostile snippets through it and check the result from outside. Present it as
"I'm now pointing the real sandbox at hostile code on purpose."

**Say plainly:** this is containment against accidental and AI-written side effects, not protection
against a determined attacker, and it has not had an independent review.

### Answering the questions
They are tiny arithmetic questions about `apply_discount`, `late_fee` and `invoice_total` (see the
file `pkg/pricing.py`); work them out on camera. Questions differ slightly from run to run. Type
the letter, or the value. To get one wrong on purpose, pick any other option once, early.

### Honest close (say this)
"This is an experimental release. The score is not validated, quizzes cannot stop someone pasting
the questions into an AI, and the sandbox has not had an independent review. It gives visibility,
not proof. Links and limits are in the README."

## What is staged, so you can say so

- The "AI" in the setup is a stand-in (`git-ai checkpoint mock_ai`) so the demo repeats. With a real
  agent that has Git AI hooks, the same notes appear on their own.
- `DEMO_FORCE_FOLLOWUP=1` makes the timed follow-up always appear.
- The PR comment and check run need a GitHub repository; to show them, push `feature` to a real
  repo, open a pull request, and the Action posts the comment. Do not claim the Action ran if you
  did not show it.

## If something goes wrong

| symptom | fix |
|---|---|
| `cdebt agent connect` says the CLI is not found or "Not logged in" | log in once: run the Claude Code CLI and finish `/login`; or skip this step and the built-in questions are used |
| the hook prints "No terminal is attached" | run the push from a real terminal window, not from a script |
| nothing is quizzed | the AI change is not on `feature`, or it is already understood: re-run the setup |
| the setup says the folder exists | `rm -rf ~/cdebt-demo ~/cdebt-demo-remote.git`, then run it again |
| Docker error | you do not need Docker on a Mac: the strict macOS sandbox is used |

## Things not to claim

Do not say it is validated, secure against a determined attacker, or a way to catch cheating. It
is a visibility tool with the limits listed in `docs/LIMITS.md`.

## The animated walkthrough (a ready-made cut)

`examples/first_user/animation/walkthrough.html` is a self-contained animated version of this
story (about 80 seconds, no internet needed). Every line of terminal text and the pull-request
comment come from the real rehearsal (`assets/`). To turn it into a video file:

1. Open it in Chrome and go full screen (Ctrl+Cmd+F). It scales to any screen.
2. Press **R** to restart, then record with **Cmd+Shift+5 -> Record Entire Screen** (QuickTime also works).
3. Keys: **Space** pause, **R** restart, **left/right arrows** previous/next scene, **H** hide the hint.

Use it as the intro or the closing montage, with your live terminal recording in the middle. The
pull-request comment scene is a re-rendered view of the real bot comment (see `assets/README.md`);
say "this is what the bot posts" rather than presenting it as a live capture.
