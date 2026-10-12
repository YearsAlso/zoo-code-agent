# zoo-code-agent

[**English**](README.md) | [中文](README.zh.md)

A minimal coding agent built on [zoo-framework](https://github.com/YearsAlso/zoo-framework) — a
mini-claude-code.

## What this is

- **The layout is a standard scaffold project.** `src/` is the project root, `src/main.py` is the
  entry point, and `src/{workers,conf,params,events}/` are the framework's four extension points —
  `zfc --worker` works inside this repository as-is.
- **It is not part of the framework.** It only depends on the released `zoo-framework`
  (`>=0.12,<0.13` from PyPI) and does not modify it.
- **Two purposes:**
  1. **Demonstration** — how to build a basic agent on the framework.
  2. **Validation** — the first real consumer of the framework's "Agent" track
     (zoo-framework issue #32), turning paper requirements into falsifiable findings.

## Layout

```
src/
├── main.py                 Entry: no args → start the scheduler; -p → one session; --eval → benchmark
├── workers/                Workers — the framework's task execution unit
│   ├── agent_worker.py     Runs one agent session from a standard Worker (out-of-process; the why is in the file)
│   └── sample_worker.py    The scaffold's own example
├── conf/demo_conf.py       @configure — startup hook
├── params/demo_params.py   @params — binds config.json values to class attributes
├── events/demo_event.py    @event — event reactor
├── agent.py  loop.py  model.py  providers.py  permissions.py
├── runtime.py  session.py  tools.py  context.py  cli.py
└── evals/                  Task-level benchmark
```

## Quick start

```bash
pip install -e ".[dev]"     # no real model SDK included
pytest                      # 108 cases, none of which need an API key
python src/main.py          # start the scheduler and run the example workers (Ctrl-C to stop)
python src/main.py -p "list the current directory" --script examples/smoke.json --allow list_dir   # key-free session
```

With a real model (needs `ANTHROPIC_API_KEY`):

```bash
pip install -e ".[dev,anthropic]"
python src/main.py -i       # interactive session
```

Or use the engine's library API directly — in a scaffold layout `src/` is on the path, so the
modules are top-level:

```python
import sys

sys.path.insert(0, "src")

from agent import build_master, drive_session
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

python src/main.py                                                # scaffold default: start the scheduler and its workers
python src/main.py -i                                             # interactive session (needs ANTHROPIC_API_KEY)
python src/main.py -p "make the tests pass" --allow write_file,edit_file,run_shell   # headless single run
python src/main.py --json -p "make the tests pass" --allow run_shell     # one JSON event per line
python src/main.py -p "list the current directory" --script examples/smoke.json --allow list_dir  # key-free demo
python src/main.py --eval                                          # task-level benchmark
```

**No arguments means the scaffold default** — start `Master`, register the workers in
`src/workers/`, keep running. It does *not* enter an interactive session; use `-i` for that.

**Exit codes** say why the run ended, so scripts can branch on them: `0` finished / `2` budget
exhausted / `3` deadline reached / `4` timeout / `5` error.

**Read-only by default.** With no pre-authorization every write or exec call is refused. To let it
change code, pass `--allow`; in a trusted environment you can use `--approve-all`. There is no
interactive approval UI yet, and [`src/permissions.py`](src/permissions.py) says so in its module
docstring.

**Streaming is step-level**, not token-level: each model response and tool result is printed as it
happens, while `ModelClient.complete` still returns a whole response at once.

**This repository is itself a standard scaffold project** (see [Layout](#layout)), so `zfc --create`
and `zfc --worker` work here directly; [`src/workers/agent_worker.py`](src/workers/agent_worker.py)
shows how to run an agent session from a standard Worker.

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

## Task-level benchmark: what it can do, in numbers

```bash
python src/main.py --eval                            # all tasks, scripted model, no key
python src/main.py --eval --task bugfix-01 --repeats 3
python src/main.py --eval --real-provider            # real model (needs an API key)
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

### What it can and cannot do (honestly)

**Can** (verified under the scripted plan): read the test → find the source → change one line → run
the test again, with every step going through the permission gate; fix a bug, implement a function,
fix empty-input handling; resume a session across a process restart without repeating a replayed
side effect.

**Cannot** (current version, stated plainly): pass by editing the test (the benchmark fails you —
that is the design, not a defect); there is no interactive approval UI (write/exec rely on
pre-authorization or an approver callback, and the default denies everything); there is no sandbox
(`run_shell`'s boundary is the working directory plus the permission gate, not isolation); the real
model path is untested here.

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
`.claude/rules/assertion-integrity.md` warns about.

## Status

All eight changes are delivered and **archived** (`openspec/changes/archive/`); their deltas were
merged into the 11 capability specs under [`openspec/specs/`](openspec/specs/) — those are the
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

- Tests: **108 cases** green, `ruff` clean; `openspec list` shows no active change and all 11
  baseline specs pass `--strict`.
- Base: `zoo-framework>=0.12,<0.13`, `requires-python>=3.11`.

## Relationship to the framework repository

This repository is mounted as a git submodule at `example/agent` of zoo-framework. After cloning the
framework you need `git submodule update --init` to see it.
