# Tasks — fix-doc-drift

## A · 测试守住数字

- [x] A1 `tests/test_docs.py`：用例数 MUST 等于全量收集数（子进程收集，故"只跑这个文件"也成立）
- [x] A2 同文件：能力规格数 MUST 等于 `openspec/specs/*/spec.md` 份数
- [x] A3 同文件：已归档变更数 MUST 等于 `openspec/changes/archive/` 下的目录数
- [x] A4 三份文档里**每一处**这类数字都比对；一处数字都找不到时也判失败
- [x] A5 反向验证：把 ROADMAP 的 220 改成 219，测试确实红（报 `[219, 220] vs 220`），已改回

## B · 文档改对

- [x] B1 README.md：108→220、11→14、eight→16 + Status 表补 8 行
- [x] B2 README.zh.md：同上（含"七个变更"→"16 个变更"）
- [x] B3 ROADMAP.md：基线行与页尾两处

## C · 验证

- [x] C1 全量 220 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] C2 `openspec validate fix-doc-drift --strict` 通过
