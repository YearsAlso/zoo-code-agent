<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg"/>
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo.svg"/>
  <img src="docs/assets/logo.png" alt="zoo-code-agent" width="300"/>
</picture>

**zoo-code-agent** — a coding agent you run yourself, on your own repository. It reads your files,
edits them and runs your tests, with a permission gate in front of every write.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](pyproject.toml)
[![CI](https://github.com/YearsAlso/zoo-code-agent/workflows/CI/badge.svg)](https://github.com/YearsAlso/zoo-code-agent/actions/workflows/ci.yml)
[![Roadmap](https://img.shields.io/badge/roadmap-Now%20%2F%20Next%20%2F%20Later-informational)](ROADMAP.md)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

[**English**](README.md) | [中文](README.zh.md)

</div>

---

## What this is

**Small is the design, not the maturity.** Everything this agent does is pinned by a spec under
[`openspec/specs/`](openspec/specs/) and covered by tests (492 cases); a
[task-level benchmark](#task-level-benchmark-what-it-can-do-in-numbers) reports what it can and
cannot do — **including the runs where it fails**. Keeping it small is what lets you read the whole
thing before you point it at your repository.

- **A real agent, not a sketch.** It reads files, edits them and runs your tests; a session survives a
  process restart; and the exit code says why it stopped (`0` finished / `2` budget exhausted /
  `3` deadline / `4` timeout / `5` error).
- **Writes are gated by default.** With no pre-authorization the agent is read-only: every write or
  exec call is refused unless you grant it (`--allow`, an approver callback, or `--approve-all` in a
  trusted environment).
- **The layout is a standard scaffold project.** `src/` is the project root, `src/main.py` is the
  entry point, and `src/{workers,conf,params,events}/` are the framework's four extension points —
  `zfc --worker` works inside this repository as-is. The engine is grouped into **five layers**
  under `kernel/` `providers/` `tools/` `engine/` `surfaces/`, and dependency direction, acyclicity
  and per-file size are guarded by `tests/test_architecture.py` (see [Layout](#layout)).
- **It is not part of the framework.** It only depends on the released `zoo-framework`
  (`>=0.12,<0.13` from PyPI) and does not modify it.
- **It also validates the framework** — it is the first real consumer of the framework's agent story
  (issue #32), and the gaps it found are filed as framework issues (see
  [Four findings](#four-findings-from-actually-running-it-and-where-they-landed)).

## Layout

```
src/
├── main.py                 Entry: no args → an interface in a terminal (scheduler otherwise); -p → one session; --eval → benchmark
├── workers/                Workers — the framework's task execution unit
│   ├── agent_worker.py     Runs one agent session from a standard Worker (out-of-process; the why is in the file)
│   └── sample_worker.py    The scaffold's own example
├── conf/demo_conf.py       @configure — startup hook
├── params/demo_params.py   @params — binds config.json values to class attributes
├── events/demo_event.py    @event — event reactor
├── kernel/                 ① Contracts: depends on no other engine module
│   model.py permissions.py agent_events.py hooks.py session.py
│   context.py prompting.py skills.py sandbox.py version.py
├── providers/              ② Transport: how a model is called, classified, retried
├── tools/                  ③ Tools: contract, registry, built-ins, plugins, MCP
├── engine/                 ④ Runtime: session state, the loop, dispatch, agent wiring, subagents
├── surfaces/               ⑤ Surfaces: cli / shell / render / tui / rpc / sdk
└── evals/                  Task-level benchmark
```

**The dependency direction is guarded by a test** (`tests/test_architecture.py`): layers point
downwards only, the graph must be acyclic (**deferred imports count**), and every file is under a
line-count ratchet. That test is also what makes this section of the README a claim rather than
decoration.

## Quick start

```bash
pip install -e ".[dev]"     # no real model SDK included
uvx --from git+https://github.com/YearsAlso/zoo-code-agent zca --version   # try it without cloning (0.0.1 today)
pytest                      # 492 cases, none of which need an API key
python src/main.py          # start the scheduler and run the example workers (Ctrl-C to stop)
python src/main.py -p "list the current directory" --script examples/smoke.json --allow list_dir   # key-free session
```

**Version and release.** `zca --version` prints the version, which has exactly one source: the installed
distribution's metadata (`pyproject.toml` when run in place). `CHANGELOG.md` keeps the history; pushing a
`v*` tag runs the release workflow, which refuses to publish unless the tag and the declared version
agree, runs the whole suite first, and installs the wheel it just built before uploading it (that last
step is what catches missing files and broken entry points). Publishing uses trusted publishing, so there
is no long-lived token in the repository. The wheel builds and installs locally — the upload itself has
not been exercised here, since that needs a tag push and the PyPI-side configuration.

With a real model (needs `ANTHROPIC_API_KEY`):

```bash
pip install -e ".[dev,anthropic]"
python src/main.py -i       # interactive session
```

Or use the engine's library API directly — in a scaffold layout `src/` is on the path, so the
five layer packages are themselves top-level:

```python
import sys

sys.path.insert(0, "src")

from engine.agent import build_master, drive_session
from providers import AnthropicClient

master = build_master(
    model=AnthropicClient(system="You are a read-only file assistant."),
    session_id="s1",
    prompt="List the current directory, then read README.md",
    root=".",
    budget=20000,
)
print(drive_session(master, "s1"))
```

## Command line

```bash
uvx --from git+https://github.com/YearsAlso/zoo-code-agent zca --help   # try it without cloning
uvx --from "zoo-code-agent[eval]@git+https://github.com/YearsAlso/zoo-code-agent" zca --eval  # benchmark (needs pytest)

python src/main.py                                                # in a terminal: an interface (full-screen with the tui extra, else the line shell)
python src/main.py --ui workers                                   # explicitly ask for the scaffold default: scheduler + workers
python src/main.py -i                                             # the line-mode interactive session (needs ANTHROPIC_API_KEY)
python src/main.py -i --script examples/smoke.json --banner        # the same shell, no keys needed
python src/main.py -p "make the tests pass" --allow write_file,edit_file,run_shell   # headless single run
python src/main.py --json -p "make the tests pass" --allow run_shell     # one JSON event per line
python src/main.py -p "list the current directory" --script examples/smoke.json --allow list_dir  # key-free demo
python src/main.py --eval                                          # task-level benchmark
```

**No arguments means the scaffold default** — start `Master`, register the workers in
`src/workers/`, keep running. It does *not* enter an interactive session; use `-i` for that.

**Exit codes** say why the run ended, so scripts can branch on them: `0` finished / `2` budget
exhausted / `3` deadline reached / `4` timeout / `5` error.

**Read-only by default — and writes ask first.** In a terminal, every write or exec call stops for a
y/N; the prompt shows the tool, and **a unified diff of what is about to change** (writes and exact
edits both produce one) instead of asking you to bless a whole file. Summaries are length-capped, so
a few-thousand-line file cannot flood the terminal. With no pre-authorization *and* no terminal (a
pipe, CI, `--json`) the call is **refused** rather than queued — waiting for input that will never
come is worse than failing. `--allow write_file,edit_file,run_shell` pre-authorizes, and
`--approve-all` removes the gate entirely (trusted environments only).

That prompt is a **deferred decision**, not a blocking read: the call is parked in session state, a
request goes out on a topic, and a decision comes back on another one. The event thread is never
held hostage by a human — and "waiting for approval" is a real session phase you can query.

**Transient model failures are retried; the rest are not.** A rate limit or a connection blip is
retried with exponential backoff — 3 retries and a 1s base by default, `--retries` / `--retry-delay`
to tune, `--retries 0` to see the first failure raw. An auth failure or a bad request fails
immediately: retrying a deterministic failure only buys seven seconds of waiting and a few more
doomed requests. Every retry prints one line saying why and for how long, because a silent seven
second pause is indistinguishable from a hang. The SDK's own retry is switched off so this stays the
single, observable source of truth. A **streaming** call stops retrying the moment it has emitted
text — replaying it would show the same sentence twice, and that looks like success.

**Point it at your own endpoint.** `--provider openai --base-url https://your-gateway/v1 --model ...`
speaks the OpenAI-compatible `/chat/completions` protocol (vLLM, a gateway, a relay, the official
API — same code path), with the key read from `OPENAI_API_KEY`. It is built on the standard library
alone, so "the default install runs and can be asserted" still holds for it. Streaming and retries
are the layers around it and apply automatically — adding this provider touched neither the loop nor
the renderer nor the retry policy.

**Bring your own tools.** Two ways in, both landing in the *same* registry as the built-ins, and so
through the same permission gate: an installed package can declare tools via the
`zoo_code_agent.tools` entry point (installed is trusted), or you pass `--tool-module my_tools` (the
module exposes `TOOLS`). A declaration inside the working directory (`.zoo/tools.py`) is the third
form and needs `--trust-project-tools` — it means running a file from that directory inside the
session process, so silence would be the wrong default; without the flag it fails with a message
saying what to do. Registration also enforces two rules: a tool that declares a side effect must
require approval, and a name clash is refused rather than silently overridden.

Tools now declare whether repeating them is **safe** (default: no). A tool that does not say so is
never retried by the reactor; `run_shell` and `edit_file` say no (a second run means a second deploy,
or a different outcome), while the read tools, `write_file` and the ledger-backed paid tool say yes.

**Or attach an MCP server.** `--mcp 'python my_server.py'` (repeatable) speaks a minimal stdio MCP:
handshake, list tools, call tools, one JSON message per line. Those tools land in the same registry as
everything else, so they go through the same gate — and the permission mapping is **fail-closed**: only
a tool the server explicitly marks read-only (`annotations.readOnlyHint`) skips approval; everything
else counts as side-effecting, and `idempotentHint` decides whether a failure may be retried. Every
request has a wall-clock limit, so a server that stops answering fails instead of hanging the session,
and the processes this command starts are the ones it terminates.

**Streaming is token-level where the client supports it.** `ModelClient.complete` stays the
contract and `stream` is an *optional* capability next to it, so a client that only implements
`complete` keeps working — the loop takes the streaming path when it is there and the whole-response
path when it is not. Deltas arrive as their own `model_delta` events; the terminal prints them in
place, while the pipe renderer ignores them entirely (byte-identical output) and `--json` gets them
as extra lines. The OpenAI-compatible path is exercised end to end against a **local server** that
speaks the protocol (`tests/fake_provider.py`) — real urllib requests, real SSE, real status codes,
including a whole session with a tool call and a `--provider openai` command line run. What that does
*not* prove is how any particular vendor behaves (its rate limits, its wording, its streaming habits);
that needs a key, and it has not been measured. The Anthropic path got the same treatment for the same
reason: a local server speaking the Messages protocol, driven by the **real SDK** (complete, streaming
with fragmented tool inputs, rate limits, and a whole session).

**This repository is itself a standard scaffold project** (see [Layout](#layout)), so `zfc --create`
and `zfc --worker` work here directly; [`src/workers/agent_worker.py`](src/workers/agent_worker.py)
shows how to run an agent session from a standard Worker.

## The interactive shell (`-i`)

It prints a banner (mark, working directory, model, budget) and then:

- plain input is a prompt; a **trailing `\` continues the line**, so pasting several lines is one
  prompt instead of several turns
- `/` starts a command: `/help` `/cost` `/status` `/sessions` `/resume <id>` `/new`
  `/allow <tool>` `/skills` `/logo` `/entries` `/show <id>` `/fork <id>` `/exit`
- **Ctrl-C interrupts the current turn** and returns to the prompt; leave with `/exit` or Ctrl-D
- on a real terminal it uses stdlib `readline`: history lands in `.zoo/history`, and `/commands`
  and `@paths` complete on Tab
- writes and execs show **a unified diff** first: `y` allows this one, `a` allows that tool for the
  rest of the session, anything else refuses

Appearance degrades by capability rather than assuming a terminal: `--renderer auto|plain|ansi|rich`,
`--color auto|always|never`, `--banner` / `--no-banner`. Three things worth knowing:

- `plain` is the **pipe default** and is byte-identical to the output from before the shell existed;
  under `--json` stdout carries one JSON object per line and nothing else (no banner, no status line)
- when the stream cannot encode block characters (`cp1252` / `gbk` on Windows) the banner falls back
  to plain ASCII instead of raising
- `rich` is an **optional** enhancement (`pip install 'zoo-code-agent[tui]'`): nicer when present,
  the built-in ANSI renderer otherwise — both render the same instructions and pass the same tests

## What a session consists of

Four capabilities — `agent-loop` / `model-client` / `tool-invocation` / `session-persistence` — with
**the authoritative specs under [`openspec/specs/`](openspec/specs/)** (`<capability>/spec.md`, MUST/SHALL clauses plus scenarios). The essentials:

- **Loop**: a `BaseWorker` subclass with `is_loop`; one `_execute` advances one step. Three
  termination conditions (budget / deadline / the model stops asking for tools), first one wins.
- **Tools**: a declaration plus a handler. Read-only (read file / list dir / `grep` contents /
  `find` names) and approval-gated side effects (write file / exact edit / run command / a paid
  stub). Paths are normalized before being checked against the root.
- **Permissions**: a tool that needs approval is refused when there is no approver (fail-closed),
  and a refusal is not an exception — it is fed back to the model as an observation. **Out of the
  box the agent is read-only**; to let it change code you must grant pre-authorization or supply an
  approver.
- **Delivery**: tool calls go through the framework's event pipeline, never a direct call inside the
  Worker body.
- **State**: session records and the idempotency ledger are persisted by the framework's state
  machine, so **a session resumes after a process restart** (see
  [`session-resume`](openspec/specs/session-resume/spec.md)).
- **Context**: the system prompt is layered (base behaviour → project instructions → available
  tools); `AGENTS.md` / `CLAUDE.md` found above the working directory are injected automatically;
  when the history passes a threshold the middle is folded into **one summary observation** (the
  goal, the files touched, the commands run) while **the persisted record stays intact**; the budget
  counts estimated input plus reported output.
- **Hooks**: three lifecycle extension points — `pre_tool` (deny, or **rewrite arguments**),
  `post_tool` (observe the result), `on_stop` (session teardown). Off by default.
- **Skills**: progressive-disclosure instruction packs under `skills/<name>/SKILL.md`; the prompt
  lists only the name and one line, and `read_skill(name)` loads the body on demand.
- **Events, phase, cancel**: the loop publishes `model_response` / `tool_result` on a topic and one
  stop event on another, so consumers subscribe instead of polling. The session records an explicit
  `phase`, and Ctrl-C becomes a cooperative cancel (exit code `6`).
- **Batch dispatch**: `--tasks <file>` hands each prompt to the Master as a runtime-registered
  one-shot worker; results come back on the framework's result topic, capped by `--concurrency`.

## Extending it: lifecycle hooks

The permission gate answers *whether* a call may run; a `pre_tool` hook answers *how*. The gate runs
first, so a hook can never overturn its refusal. `post_tool` / `on_stop` are observe-only.

```python
from kernel.hooks import Hooks, PreToolDecision

def block_migrations(name, arguments):
    if str(arguments.get("path", "")).startswith("migrations/"):
        return PreToolDecision(allow=False, reason="migrations/ is out of scope")
    return PreToolDecision()                       # or PreToolDecision(arguments={...}) to rewrite

seen = []
hooks = Hooks(
    pre_tool=block_migrations,                     # one callback each — no multicast / priority / async
    post_tool=lambda name, args, result: seen.append((name, result)),
    on_stop=lambda reason, summary: print("stopped:", reason),
)
master = build_master(model=client, session_id="s", prompt="...", root=".", hooks=hooks)
```

A hook raising is **failure-closed**: `pre_tool`'s exception denies the call and the reason carries
it; `post_tool` / `on_stop` exceptions are swallowed, because by then the result and the stop reason
are already committed.

## Write your own skill

A skill is a folder of instructions loaded **on demand** — the prompt lists only its name and one
line, and the model pulls the body in with one tool call. Put it under `skills/<name>/SKILL.md`:

```
skills/
└── migration-check/
    └── SKILL.md
```

```markdown
---
name: migration-check
description: 改数据库 schema 时，先核对迁移脚本再动 ORM 模型
---
1. Find the migrations directory and list existing scripts.
2. New migrations must be idempotent (`IF NOT EXISTS`, `DO $$ ... $$`).
3. Ship a rollback script; wrap the DDL in one transaction.
```

The system prompt gets `migration-check: <description>` and nothing else; calling
`read_skill("migration-check")` returns the body as a tool observation. The tool is read-only, so
the permission gate lets it through by default — and a `pre_tool` hook can still gate it if you want
(see the hooks section above). An unknown name errors and lists what is available; it never returns
an empty body.

The description line's fallbacks: omit the frontmatter and the name defaults to the folder name,
the description to the first non-empty line of the body.

## Events, phase, and cancelling

A run is observable by **subscribing**, not by polling. The loop publishes `model_response` /
`tool_result` on an event topic and one stop event on a stop topic; `drive_session` subscribes, and
the CLI and the benchmark just render what arrives. Delivery is synchronous and ordered — the
channel FIFO (used for tool *work*) hands reactors to a thread pool and does not preserve order, so
events take the ordered dispatch path. In short: the queue dispatches work, topics carry
notifications.

The session also carries an explicit `phase` (`thinking` / `tools_pending` / `stopped`), so "what is
it doing right now" is answerable from state instead of by reading the record log.

Ctrl-C becomes a **cooperative cancel**: it sets a state flag, the loop stops at the next boundary,
and the process exits with code `6`. It does **not** interrupt a tool call already in flight —
CPython cannot safely kill a thread, so the agent does not pretend to. Library users open the same
door with `AgentRuntime().request_cancel(session_id)`.

Same-round tool calls now run **one at a time**, in the order the model gave them: the framework
would happily execute them concurrently, so serialising them is the loop's own contract.

## Batch: let the Master dispatch tasks

One process used to mean one session: the agent registered a single long-lived loop worker and drove
the scheduler itself. A batch of prompts now goes through the framework's own dispatch path:

```bash
python src/main.py --tasks tasks.txt --concurrency 2 --allow read_file,edit_file,run_shell
```

Each line of `tasks.txt` becomes a **one-shot worker** (`is_loop=False`) registered into the Master
at runtime. `Master.register_worker` adds it to the scheduling list, the scheduler dispatches it, and
because it is one-shot the scheduler drops it again when it finishes — nothing to reap by hand.
Results come back on the framework's unified result topic (`waiter`), not by polling session state.

The dispatcher owns exactly three things: feeding the queue, topping up while a slot is free, and
collecting results in input order. The concurrency cap lives there too, because the thread-per-task
model this repo uses has no bound of its own. A task that raises becomes that task's own error
result — the framework logs a worker failure and does *not* emit a result event, so without that step
the whole batch would sit until its timeout.

## Task-level benchmark: what it can do, in numbers

```bash
python src/main.py --eval                            # all tasks, scripted model, no key
python src/main.py --eval --task bugfix-01 --repeats 3
python src/main.py --eval --real-provider            # real model: Anthropic (needs ANTHROPIC_API_KEY)
python src/main.py --eval --real-provider --provider openai --base-url https://your-endpoint/v1 --model <id>   # or any OpenAI-compatible endpoint
```

Three tasks cover three kinds of change: fix a bug (`bugfix-01`), implement a stub (`feature-01`),
fix empty-input behaviour (`edgecase-01`). Judging is **independent of the agent** — it runs `pytest`
in a sandbox and compares the content hash of the exam files, so **editing the test counts as
failure**.

| Task | Result | Turns | Tool calls | Tokens | Wall clock |
|---|---|---|---|---|---|
| `bugfix-01` | pass | 5 | 4 | 40 | 1.80 s |
| `feature-01` | pass | 5 | 4 | 40 | 1.13 s |
| `edgecase-01` | pass | 5 | 4 | 40 | 1.85 s |
| **median** | **3/3** | 5 | 4 | 40 | **1.80 s** |

> Numbers come from one run on 2026-10-12 (Windows / Python 3.13, scripted model, no network
> latency); the raw JSON is [`bench/results/eval-scripted.json`](bench/results/eval-scripted.json).
> **Success rate with a real model has not been measured** — that needs an API key and is recorded
> separately.

**How to fill that row in** (the wiring is in place; only the key is missing):

1. either provider works: `--eval --real-provider` (Anthropic) or
   `--eval --real-provider --provider openai --base-url <endpoint>/v1 --model <id>`;
2. use `--repeats N` with N >= 3 (one run's luck should not become a conclusion) and a different
   `--out` path, so the scripted baseline is not overwritten;
3. **list the failures too** — they carry more information than the success rate — and give a token
   cost magnitude;
4. this path is already verified end to end against a local fake server (all three tasks, both
   provider paths, and a broken provider still failing the benchmark), so anything that goes wrong
   while filling in numbers will be on the key or the vendor-behaviour side.

### What it can and cannot do (honestly)

**Can** (verified under the scripted plan): read the test → find the source → change one line → run
the test again, with every step going through the permission gate; fix a bug, implement a function,
fix empty-input handling; resume a session across a process restart without repeating a replayed
side effect.

**Cannot** (current version, stated plainly): pass by editing the test (the benchmark fails you —
that is the design, not a defect); count on isolation being there (see the boundary table below —
it is best-effort and this machine cannot apply one); the real model path is untested here.

### Where the boundary comes from (three tiers)

The permission gate decides *whether* a command runs; it says nothing about what that command can
then touch. That second layer is the sandbox, and it is explicit about being best-effort:

| Tier | What it gives you | How to get it |
|---|---|---|
| `sandbox-exec` (macOS) | writes confined to the working directory and temp dirs; network allowed or cut | nothing to install — it ships with the system |
| `bwrap` (Linux) | same, via bubblewrap (`--ro-bind / /` plus a writable bind of the root) | `apt install bubblewrap` or your distro's equivalent |
| none | **only** the permission gate: the boundary is *authorization, not isolation* | `--sandbox none`, and what `auto` falls back to |

**Availability is decided by actually running a sandboxed `exit 0`, not by looking for the binary.**
That distinction is not hypothetical: on the machine this was written on, `/usr/bin/sandbox-exec`
exists but refuses to apply (`sandbox_apply: Operation not permitted` — it was itself inside a
sandbox). Trusting `which` would have announced "sandbox enabled" and then failed every single
command with an error nobody could act on. So when the probe fails, the session says so up front:
the banner and `/status` both print the effective boundary.

`--sandbox auto|strict|none` picks the tier (`auto` by default) and `--sandbox-no-network` cuts the
network inside the boundary. **What it does not do**: reads are *not* restricted (the agent has to
read system files to work), and the network is all-or-nothing rather than a domain allowlist. Those
choices are written down rather than hidden, and real isolation is **unmeasured on this machine** —
the test that checks it skips with that reason stated, so the claim stays checkable elsewhere.

### Forking a session

Trying two approaches is the normal way to work, so being able to go *back* matters. Every session
record now carries a stable address (`e1`, `e2`, …), and `--fork <session>:<entry>` starts a new
session whose history stops at that entry — the original is left **byte-for-byte untouched**, which is
the whole point (you can compare the two). The forked session also inherits the idempotency ledger and
the token accounting, so replaying an already-paid effect does not pay twice and "what did this branch
cost" stays true. What it does *not* inherit: in-flight state (a parked approval, an unfinished call) —
copying that would leave the new branch waiting for a decision that can never arrive. And forking
copies **session records, not process memory** — MCP servers and environment of the original process
are not reproduced.

`/entries` lists what is addressable, `/show <id>` prints one record, and `/fork <id>` branches in
place (switching the shell onto the new branch). Those addresses also make history compaction
honest: a folded summary names the entries it covers (`covers`), and each of them is still readable
from the archive — "the raw entries are still there" is only useful if you can point at one.

One thing to know about the CLI: a plain run starts from a **clean** state (it does not silently pick
up yesterday's session), so `--fork` is the one path that deliberately reads the archive. Inside the
shell, `/sessions` and `/resume` do that job within one process, and `/fork <id>` branches in place.

### Subagents: `--subagent`

Some work wants a lot of exploration that you never want to see. `--subagent` adds a `subagent(task)`
tool that hands one task to a **separate process** and brings back only three things: the conclusion,
the evidence (which tools ran, why it stopped) and the usage (tokens, records, wall clock). The child's
full transcript stays in its own session — dumping it into the parent is exactly the context explosion
this avoids; read it later by session id if you need the detail.

Three properties are worth knowing, because they are the point of using a process at all:

- **Isolation**: the child runs with a temporary working directory, so the framework's state file
  (resolved relative to cwd) cannot collide with the parent's, while `--root` still points both at the
  same repository.
- **Its own gate**: the child's permission gate is its own and fail-closed. Whatever the parent
  pre-authorized does *not* leak into the child; you have to pass it explicitly.
- **Failure is an observation**: a non-zero exit, a timeout, or a child that produced no conclusion
  comes back as a tool failure carrying the exit code and the tail of the child's stderr — the parent
  session keeps going.

**Budgets here are ceilings, not shares.** The parent passes a token budget and a wall-clock limit and
the child enforces its own; there is no shared pool that decrements in real time. That would need the
two processes to share a runtime ledger, which is the same problem as zoo-framework#248 — so this is
stated rather than faked.

### The full-screen interface: `--tui`

![The full-screen interface](docs/assets/tui-preview.svg)

*(A real render exported by Textual itself, not a mockup — it carries both the light and the dark theme.)*

`pip install 'zoo-code-agent[tui]'` then `zca --tui` gives the same session a shape on screen: the
transcript in one place, tool calls in another, a status line (busy mark, phase, turns, tokens, model,
working directory, boundary) and a **framed input area** — the `❯` prefix marks where to type, and
the key hints sit permanently on the line below, listing **only the keys that work right now**
(while an approval is pending it does not still say "Enter submits"). Submitted inputs come back with
`↑` / `↓`, and coming back down lands on the draft you were writing. The keys and semantics are deliberately the same as the line-mode shell —
`Ctrl-C` interrupts the *turn*, `Ctrl-Q` leaves, and approvals answer `y` / `a` / `n` through the very
same `PermissionPolicy` object. It is another **consumer of the same event stream**, not a second
implementation: the module has no loop of its own.

The interesting part is how it is testable. The screen state is a **pure reduction** — `apply_event`
folds one event into an immutable state, and `render_lines` projects that state into the lines that
should be on screen. So "what the screen shows" is asserted in CI **without a terminal and without the
UI library**, and the Textual layer is thin enough that two headless tests (`run_test()`) can drive the
real widget tree: type a prompt, watch the scripted model answer appear, press `Ctrl-Q` to leave.
Without the extra installed, `--tui` prints one line telling you what to install instead of a traceback.

### Driving it from other programs: `--rpc` and the SDK

`--rpc` makes the process long-lived and speaks **JSONL over stdio**: one JSON request per line in, one
JSON object per line out. Methods are `prompt`, `cancel`, `status` and `stop`; responses carry the
request's `id`, and **event lines are exactly the `--json` events** — the same payloads through the same
renderer, so there is no second schema to drift. Two tests keep that honest by running the same scripted
session both ways and asserting the event sequences match.

```bash
printf '%s\n' '{"id":1,"method":"prompt","text":"list the files"}' '{"id":2,"method":"stop"}' | zca --rpc
```

For Python in the same process there is `src/surfaces/sdk.py`:

```python
from surfaces.sdk import open_session

session = open_session(model=my_client, root=".", session_id="embed")
print(session.send("list the files")["stop_reason"])
session.close()
```

Both entry points only *assemble* — they reuse `build_master` / `drive_with_renderer` / the session store.
That is a checkable property rather than a promise: if either module ever grows its own turn loop, the
same-source tests are the first thing to break. Two things to know: since neither has a human to ask,
**approvals are fail-closed** (`--allow` still pre-authorizes), and the runtime is a **process-level
singleton** — assembling a second session in one process needs `AgentRuntime().reset()` first.

### One measured lesson: turning on the push model is not enough

The event pipeline has two time parameters: `event:pushModelEnabled` (the producer wakes the consumer
as soon as it enqueues) and `event:delay` (the interval after each drain). **With the push model on
but the default `delay`, every event after the first still waits up to one tick (5 s)** — the same
batch of tasks went from 1.9 s to 16.7 s. Both have to be set:

```json
{ "event": { "pushModelEnabled": true, "delay": 0.05 } }
```

## Four findings from actually running it (and where they landed)

These were **found by running it**, not by reading docs. They are the real output of this repository's
"validation consumer" role and are all filed as zoo-framework issues. **Two of them are fixed and
shipped in 0.12.0**, which is what let this repository upgrade and backfill the "restart resume" that
had been moved out.

| # | Finding | Issue | State (2026-10-12) |
|---|---|---|---|
| 1 | The `@worker` decorator registers into a table nothing dispatches from | [#49](https://github.com/YearsAlso/zoo-framework/issues/49) | Deprecated and cleaned up by the framework |
| 2 | The event pipeline advanced once every 5 s, with no public knob | [#73](https://github.com/YearsAlso/zoo-framework/issues/73) | **Fixed**: `event:delay` is configurable, plus the push model |
| 3 | The framework has no "exactly once" closure | [#74](https://github.com/YearsAlso/zoo-framework/issues/74) | Kept by design: idempotency belongs to the caller |
| 4 | Loading the state file was a no-op, so state never came back | [#72](https://github.com/YearsAlso/zoo-framework/issues/72) | **Fixed**: change `fix-state-restore` |

### 1. The `@worker` decorator registers into a table nothing dispatches from

It writes the legacy `WorkerRegister`, while `Master` reads `WorkerRegistry` — which is why the
framework's own `example/threads/demo_thread.py` registers a worker that never runs. The reachable
path is `Master.register_worker`, and that requires a worker to be **constructible with no
arguments** — hence this repository returns a closure-capturing class from a factory instead of
stuffing context into a module-level global.

### 2. The event-pipeline tick (**fixed**)

In 0.8.0, `EventWorker.__init__` hard-coded `delay_time = 5`: after draining a channel it slept a full
5 seconds inside `run()` before settling, and stayed "in flight" the whole time. So **every event
dispatch waited for the next tick** — a session with tool calls took seconds rather than milliseconds,
and no `worker:pool:*` key changed that.

Since 0.12.0 (`configurable-run-delay` / `event-push-model`): `event:delay` is a public setting
(default still 5, backwards compatible) and the **push model** wakes the consumer on enqueue. This
repository turns it on in `config.json`; measured end-to-end **5205 ms → 207 ms (25.1×)**, with the
comparison script in [`bench/measure_session_latency.py`](bench/measure_session_latency.py).

### 3. No "exactly once" closure in the framework (kept by design)

Retries live in `EventReactor._execute`'s own `while attempts` loop: if the callback raises, it is
replayed. Note that "executed exactly once" in `core/waiter/dispatch_core.py` describes **bookkeeping
— a single worker does not take part in the next dispatch round** — not delivery semantics. A paid
tool without idempotency would be charged twice on replay, so this repository's `IdempotencyLedger`
sits **in the caller** rather than pushing the framework to grow a feature (that would freeze the
seam). The framework closed the issue as `not_planned`
([#74](https://github.com/YearsAlso/zoo-framework/issues/74)): idempotency belongs to the caller.

### 4. State-machine loading (**fixed**, and it imposes an assembly order)

On 0.8.0, after a session finished, `.zoo/zooStates.pic` did contain the full scope and nodes
(**writing worked**), but a new process read back nothing. The cause was in
`StateMachineManager.load_state_machines`: what got persisted was a `ThreadSafeDict`, which is **not**
a `dict` subclass, so `isinstance(state_machine, dict)` was always false and the assignment never
ran — while `_local_store_loaded` was set to `True`, and that illusion of "already loaded" blocked
retries. Since 0.12.0 the guard dispatches on the real type (`ThreadSafeDict` restored as-is, `dict`
wrapped, anything else raises `TypeError`).

**The fix imposes an assembly order**: restoration is **whole-table replacement**, and loading
happens on `StateMachineWorker`'s first execution. Writing to a session before that (this repository
used to write the first user message inside `build_master`) means the new records get replaced by the
old file — a defect that could never show up on 0.8.0. Assembly now loads first
(`_load_persisted_state`), and `StateMachineWorker` returns early because `have_loaded()` is true, so
the file is read exactly once per process.

## Notes on testing

The framework's process-level sharing is **not all inside the container**. 0.12.0 ships
`core.process_state.reset_process_state()` (container and configuration surfaces), but it
**deliberately does not reset** the following — `runtime.reset_process_state_carriers()` covers them
by category:

- two **class-attribute** registries: `EventChannelRegister._channel_map` and
  `EventReactorManager.reactor_map`
- the **module-level singleton** `WorkerRegistry` — the sneaky one: it caches instantiated workers
  **by name**, so a second test case registering the same name gets the first case's instance
  (whose closure holds the first case's session), which looks like "a new session never advances".

**A new mandatory step in 0.12.0**: `reset_all()` also deletes the persisted state
(`.zoo/zooStates.pic`). Now that loading works, the file left behind by a previous run is read back
wholesale on `StateMachineWorker`'s first execution and leaks across test cases — exactly the
symptom seen during the upgrade, where the end-to-end case showed duplicate `tool` records. Test
cases that need to **simulate a process restart** should use `reset_process_state_carriers()`
(which keeps the file) instead of `reset_all()`.

Missing any of these produces real false positives — the failure mode zoo-framework's
[`assertion-integrity.md`](https://github.com/YearsAlso/zoo-framework/blob/dev/.claude/rules/assertion-integrity.md)
rule warns about (that file lives in the framework repository, not in this one).

## Status

All 36 changes are delivered and **archived** (`openspec/changes/archive/`); their deltas were
merged into the 23 capability specs under [`openspec/specs/`](openspec/specs/) — those are the
authoritative behaviour specs.

| Change | Delivered | Capabilities |
|---|---|---|
| `add-minimal-agent-loop` | The minimal loop (loop, model contract, tool contract, same-process persistence) | `agent-loop` / `model-client` / `tool-invocation` / `session-persistence` |
| `add-session-resume` | Restart resume (backfilled after framework #72/#73) | `session-resume` |
| `add-coding-tools` | Write / edit / exec / search tools and the permission gate | `coding-tools` / `permission-gate` |
| `add-task-eval` | Task-level benchmark (independent judging, cost metrics, JSON output) | `task-eval` |
| `add-cli-and-headless` | Interactive / one-shot / JSON event stream, and exit-code semantics | `cli-interface` |
| `add-context-engineering` | Instruction injection, prompt layering, input-side accounting, history compaction | `context-engineering` |
| `adopt-scaffold-layout` | Converted the whole repository to the standard scaffold layout | `project-layout` |
| `add-console-entry` | Added the `zca` console entry back (`uvx` without cloning); fixed two problems that only appear when installed | `project-layout` (revised the delivery-shape clause) |
| `add-agent-event-stream` | Session events published on framework topics, an explicit session phase, cooperative cancel | `agent-loop` |
| `add-interactive-approval` | Write / exec asks first in a terminal; fail-closed elsewhere | `permission-gate` |
| `add-lifecycle-hooks` | `pre_tool` / `post_tool` / `on_stop` for third-party policies and telemetry | `agent-loop` |
| `add-skills` | On-demand instruction packs (`skills/<name>/SKILL.md`), progressive disclosure | `skills` |
| `add-task-dispatch` | One-shot task Workers dispatched by the Master, with a concurrency ceiling | `task-dispatch` |
| `defer-approval` | Approval became "park + decide by event" instead of blocking the event thread | `permission-gate` |
| `fix-entry-and-approval-preview` | The in-place entry path really runs; approvals show a unified diff | `project-layout` / `permission-gate` |
| `add-interactive-shell` | Real interactive surface: split render/input layers, slash commands, diff approvals, interrupt-the-turn, mark banner | `interactive-shell` |
| `add-token-streaming` | Token-level streaming as an optional capability; deltas published as events; a failed model call now ends the turn instead of hanging until timeout | `model-client` / `agent-loop` / `interactive-shell` / `task-dispatch` |
| `add-model-retry` | Model errors got a taxonomy; retryability is decided by class (rate limit / transient only), with backoff, a cap and a visible notice per retry | `model-client` / `cli-interface` / `interactive-shell` |
| `add-openai-compat-provider` | An OpenAI-compatible provider (stdlib only, streaming included), selectable by configuration — the loop, renderer and retry layer needed no changes | `model-client` / `cli-interface` |
| `add-tool-plugins` | External tools through the same gate (entry points, explicit modules, trusted project file); tools declare whether repeating them is safe, and retry decisions follow that | `tool-plugins` / `tool-invocation` / `cli-interface` |
| `add-mcp-client` | A minimal stdio MCP client (`--mcp`): a third-party server's tools sit next to the built-ins behind the same gate, with fail-closed permission/idempotency mapping, a per-request timeout, and no orphan processes | `mcp-client` / `cli-interface` |
| `add-shell-sandbox` | A real boundary for `run_shell` (sandbox-exec / bwrap, three tiers via `--sandbox`); availability proven by actually applying it; writes confined to the root; the boundary is reported every session | `shell-sandbox` / `coding-tools` / `cli-interface` |
| `add-session-fork` | Stable addresses for session records; `--fork session:entry` branches without touching the original (ledger and accounting come along, in-flight state does not); folded summaries name the entries they cover | `session-fork` / `session-persistence` / `context-engineering` / `cli-interface` |
| `add-subagents` | `--subagent`: one task per process, isolated state via a temporary cwd, only conclusion + evidence + usage come back, failures arrive as observations; the design also states what is *not* done (shared budget pool → framework #248) | `subagent` / `cli-interface` |
| `add-rpc-and-sdk` | `--rpc` (JSONL over stdio) and `src/surfaces/sdk.py` for embedding — both only assemble existing pieces, so the event schema is literally the `--json` one; approvals fail closed | `rpc-and-sdk` / `cli-interface` |
| `add-fullscreen-tui` | `--tui`: the same session with a shape on screen — state is a pure reduction (so screen behaviour is asserted without a terminal), and the Textual layer is thin enough for headless tests | `fullscreen-tui` / `interactive-shell` |
| `add-release-and-version` | One source for the version (`zca --version`), a real CHANGELOG, and a tag-driven release that refuses mismatched tags, builds, installs what it built, and publishes via trusted publishing | `release-and-version` |
| `fix-doc-drift` | Documentation drift: the case count, spec count and archived-change count were all wrong; they are now invariants guarded by three tests that **read the actual value and compare** (copying the number into a test is not guarding it) | `documentation-accuracy` |
| `verify-anthropic-http-path` | The Anthropic path verified against a real SDK and a local server speaking the Messages protocol; also fixed "a missing key only fails at the first request" | `model-client` |
| `verify-openai-http-path` | The OpenAI-compatible path verified end to end against a local fake provider (whole response, streaming, and error codes) | `model-client` |
| `harden-taxonomy-and-cleanup` | Error classification now covers the real SDK's vocabulary (Overloaded / Deadline / Retryable …); a subagent is reaped when the parent is interrupted | `model-client` / `subagent` |
| `add-layering-invariants` | Layering as executable assertions rather than directory shape: one-way dependencies, an acyclic graph (**deferred imports included**), and a per-file line-count ratchet; it also removed the only cycle by extracting `tui_state` while `tests/test_tui.py` stayed untouched | `project-layout` |
| `restructure-engine-into-layers` | The engine is now five layer packages (`kernel` / `providers` / `tools` / `engine` / `surfaces`) with only the entry point and the install-time bridge left at the `src/` root; `from tools import Tool` is untouched **for plugin authors**, and the only test-file changes are import paths (no assertion changed) | `project-layout` |
| `split-god-modules` | The six over-budget files (824–421 lines) are split under 400 lines each and the whitelist is empty; **no test assertion changed** — the only exceptions are four monkeypatch targets that moved with the implementation, plus one real regression an existing test caught immediately | `project-layout` |
| `polish-terminal-ui` | Bare `python src/main.py` now enters an interface in a terminal (non-terminals keep the scaffold default; `--ui` forces any of the four modes); the input area is framed with a `❯` prefix, key hints sit permanently below and list only the keys that work right now, and `↑`/`↓` recall history — it also finally guards the no-argument entry point | `project-layout` / `cli-interface` / `fullscreen-tui` |

- Tests: **492 cases** green, `ruff` clean; `openspec list` shows no active change and
  all 23 baseline specs pass `--strict`.
- Base: `zoo-framework>=0.12,<0.13`, `requires-python>=3.11`.
- What's next: [ROADMAP.md](ROADMAP.md) (中文) — Now / Next / Later, each with a verifiable
  completion criterion.

## Relationship to the framework repository

This repository stands on its own — clone it and run it. It is also vendored into zoo-framework as
the git submodule `example/agent`, where it doubles as the framework's reference consumer; after
cloning the framework, `git submodule update --init` fetches it.
