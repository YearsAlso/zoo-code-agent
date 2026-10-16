# Tasks — fix-ci-red

## A · 让用例数与环境无关

- [x] A1 `tests/test_anthropic_http.py`：模块级 `importorskip` → 模块级 `pytestmark = skipif(...)`
      （用例始终被收集，运行时才跳过）。依赖探测用 `importlib.util.find_spec`——
      用 `try: import anthropic` 会把它变成一个模块级 import，ruff 的 E402 会拦下后续导入
- [x] A2 `tests/test_eval_real_provider.py`：删掉模块级 `pytest.importorskip("pytest", ...)`
      ——空转，但同属那类写法；把它的意图留成注释
- [x] A3 `tests/test_docs.py`：新增检查「测试模块 MUST NOT 在模块级调用 `importorskip`」，
      失败时指出文件与行号
- [x] A4 用例：故意在 `tests/test_model.py` 加回模块级 `importorskip` → 检查红并指出
      `test_model.py:9`，**同一次收集的条数当场从 476 掉到 470**（正是它要防的那件事）；随即撤掉

## B · 修 CI 的断言

- [x] B1 `.github/workflows/ci.yml`：extras 作业的版本断言改用 `importlib.metadata.version`
- [x] B2 本机实测那句新断言：`OK: rich 14.3.2 / textual 8.2.8 / anthropic 1.13.0`——
      装的就是那版没有 `rich.__version__` 的 rich，所以旧断言在这里也必然失败

## C · 验证（判据本身是这次的重点）

- [x] C1 **无 extras 环境实测收集数 = 476**（本机建干净 venv、卸掉 anthropic 后跑
      `--collect-only`）——这是"判据与环境无关"的直接证据
- [x] C2 有 extras 环境收集数同样是 476
- [x] C3 `pytest` 全量全绿；`ruff check src tests bench` 干净
- [x] C4 `openspec validate fix-ci-red --strict` 通过
- [x] C5 文档里的数字**只因为新增了那条检查而 +1**（475 → 476）——它不再随环境变化，
      这正是判据修好的证据；改的是判据，不是数字本身
