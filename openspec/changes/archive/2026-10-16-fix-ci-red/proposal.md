## Why

`main` 上的 CI 是红的，而且**在我这一轮改动之前就红了**（前两次推送到 main 同样是 failure）。
本地诊断出两个各自独立、都与产品行为无关的原因：

**一、用例数的判据依赖环境。** `tests/test_docs.py` 拿"本环境 `--collect-only` 收到的用例数"
去比对 README / ROADMAP 里写的数字。而 `tests/test_anthropic_http.py` 用的是**模块级**
`pytest.importorskip("anthropic")`——没装那个 SDK 时整组用例在**收集阶段**就消失：
实测 475 → 464，差 11 条，正好是那个文件。（实测方式：`--collect-only` 逐文件比对，
两份清单只差 `tests/test_anthropic_http.py: 11` 一行。）

于是这条判据在 CI 的默认作业（刻意不装任何 extras）必然失败，而它本意守的是
"文档里的数字有没有漂移"。**判据一旦依赖环境，它管的就不是文档，是这台机器装了什么。**

**二、CI 自己的断言写错了对象。** extras 作业里那句
`python -c "import anthropic, rich, textual; print(rich.__version__ ...)"` 报
`AttributeError: module 'rich' has no attribute '__version__'`（本机在干净 venv 里实测复现）。
新版 `rich` 没有这个属性——要的是**分发包的版本**，那应该问 `importlib.metadata`。

两者都不是这次分层重构引入的；它们是"验证本身不可信"这一类缺陷，与本仓库既有的
`fix-doc-drift` 同类。

## What Changes

**A 组 · 让用例数与环境无关（`tests/`）**

- `tests/test_anthropic_http.py`：模块级 `importorskip` 换成模块级 `pytestmark = skipif(...)`——
  用例**始终被收集**（数字不再随 extras 漂移），运行时才跳过。
- `tests/test_eval_real_provider.py`：删掉那句模块级 `pytest.importorskip("pytest", ...)`。
  它是空转（能跑 pytest 本身就证明 pytest 可导入），但它是同一类写法，留着会让下面那条
  新检查没法生效。
- 新增一条检查：**任何测试模块 MUST NOT 在模块级调用 `importorskip`**。
  它守的不是风格，而是上面那条因果链——模块级跳过会让"用例数"变成环境的函数。

**B 组 · 修 CI 的断言（`.github/workflows/ci.yml`）**

- 版本断言改用 `importlib.metadata.version("rich")` 等，不再读模块属性。

## Capabilities

### Added Requirements（并入既有能力）

- `documentation-accuracy`：文档里的用例数 MUST 与环境无关——**收集阶段** MUST NOT
  因为缺某个可选 extras 而少掉用例；模块级跳过 MUST 被拒绝

## Impact

- `tests/test_anthropic_http.py`、`tests/test_eval_real_provider.py`、`tests/test_docs.py`
- `.github/workflows/ci.yml`
- 不改产品行为、不改 CLI、不新增依赖；文档里的数字（475）**不需要改**——改的是判据，
  让它在这个数字上稳定成立

## Non-goals

- 不动 `pytest.importorskip` 在**函数体内**的用法（`tests/test_tui.py` / `tests/test_render.py`
  用它按依赖跳过单条用例）——那种写法不影响收集数，是正确用法
- 不改 CI 的作业结构（默认作业刻意不装 extras 这条保证不动）
- 不追查那次 extras 安装的 `ResolutionImpossible`：本机重试即成功，是网络抖动，
  不是依赖冲突
