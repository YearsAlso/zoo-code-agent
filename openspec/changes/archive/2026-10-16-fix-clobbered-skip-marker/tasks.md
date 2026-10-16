# Tasks — fix-clobbered-skip-marker

## A · 让两条标记共存

- [ ] A1 `tests/test_anthropic_http.py`：两条 `skipif` 合并成 `pytestmark = [...]` 列表，
      放在 `_loopback_bindable()` 定义之后
- [ ] A2 注释里写明"一个模块只能有一个 `pytestmark` 赋值，写成两次会静默覆盖"——
      下一个人加第三条标记时不该再踩一次

## B · 让这类覆盖不再可能

- [ ] B1 `tests/test_docs.py`：新增检查「测试模块 MUST NOT 多次赋值 `pytestmark`」，
      失败时指出文件与第二次赋值的位置
- [ ] B2 用例（做完即撤）：在某个测试模块里临时写两条 `pytestmark` → 检查必须红

## C · 验证（这次要断言"因为哪个理由跳过"）

- [ ] C1 在**没有 anthropic 的环境**里跑那组用例，断言跳过理由里出现 **SDK 那条**——
      本机上回环那条也会命中，所以只断言"跳过了"是不够的（这正是上一次漏掉它的原因）
- [ ] C2 `pytest` 全量全绿；`ruff check src tests bench` 干净
- [ ] C3 `openspec validate fix-clobbered-skip-marker --strict` 通过
- [ ] C4 归档；推上去之后**确认 CI 转绿**（判据在 CI 上，不在本机）
