## Why

`fix-ci-red` 刚推上去，CI **依然全红**——而且这次是我造成的。

那个变更把 `tests/test_anthropic_http.py` 的模块级 `importorskip` 换成了模块级
`pytestmark = pytest.mark.skipif(没装 SDK, ...)`。但那个文件**早就有一条** `pytestmark`
（守着"本沙箱不能绑定回环端口"）。**一个模块只能有一个 `pytestmark` 赋值：后一条会静默覆盖前一条。**
于是新加的那条被原有那条盖掉，CI 的默认作业（刻意不装 SDK）里这 11 条用例照跑，
全线报 `ModuleNotFoundError: No module named 'anthropic'`。

**为什么本机没发现**：我的沙箱**恰好也不能绑定回环端口**——所以那组用例本来就会整组跳过，
我把那些跳过当成了"我这条新标记生效了"。证据链是断的：跳过这件事有两个可能的原因，
而我只看到一个结果。CI 的日志（`gh api .../jobs/<id>/logs`，58KB）才给出真正的报错：
`FAILED tests/test_anthropic_http.py::test_complete_round_trips_through_the_real_sdk`。

## What Changes

**A 组 · 让两条标记共存（`tests/test_anthropic_http.py`）**

- 两条 `skipif` 合并成一个**列表**赋给 `pytestmark`：`pytestmark = [mark_sdk, mark_loopback]`。
  它既保住了"收集阶段不消失"（`fix-ci-red` 的目的），也保住原有那条；两条的跳过理由
  也不再互相覆盖（诊断时能分清是哪种原因跳过的）。

**B 组 · 让这类覆盖不再可能（`tests/test_docs.py`）**

- 新增机械检查：**测试模块 MUST NOT 多次赋值 `pytestmark`**。它守的不是风格——
  后一条会静默顶掉前一条，而后果是"某个按依赖跳过的整组用例悄悄跑起来"，
  表现是 CI 红、本地绿。

## Capabilities

### Added Requirements（并入既有能力）

- `documentation-accuracy`：测试模块 MUST NOT 多次赋值 `pytestmark`；同一模块的多条跳过条件
  MUST 写在同一个列表里

## Impact

- `tests/test_anthropic_http.py`、`tests/test_docs.py`
- 不改产品行为、不改 CLI、不新增依赖

## Non-goals

- 不改成"每个用例自己 `importorskip`"：那会退回收集阶段的逐条跳过，把
  `fix-ci-red` 刚修好的"收集数与环境无关"又打破
- 不去动 `conftest.py` 里的全局机制：这次的缺陷完全在单个模块内
