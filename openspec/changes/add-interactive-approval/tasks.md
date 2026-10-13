# Tasks — add-interactive-approval

## A · 交互式审批

- [x] A1 判断"能否当场问"：非 JSON 模式且 stdin 是终端
- [x] A2 询问展示工具名与实参（复用 `PermissionRequest.summary`）
- [x] A3 EOF / 非肯定回答按拒绝处理

## B · 非交互失败关闭

- [x] B1 stdin 不是终端或 `--json` 时不等待输入
- [x] B2 `PermissionPolicy` 增加 `no_approver_hint`；命令行传入可操作的措辞
- [x] B3 拒绝理由里包含"非交互环境不会等待输入"与 `--allow`

## C · 与既有两档的关系

- [x] C1 `--allow` 优先于询问（用例钉住：预授权时不输出询问）
- [x] C2 `--approve-all` 行为不变

## D · 验证

- [x] D1 用例：终端里写文件先问、且询问含工具名与实参
- [x] D2 用例：回答否 / 直接回车 → 不落盘，且观察里含"被拒绝"
- [x] D3 用例：非 tty → 不等待输入、拒绝理由含 `--allow`
- [x] D4 用例：`--allow write_file` → 不询问、正常写入
- [x] D5 全量用例通过；`ruff check` / `ruff format --check` 通过
- [x] D6 `openspec validate add-interactive-approval --strict` 通过

## E · 文档

- [x] E1 两份 README：删掉"没有交互式审批界面"，改成"写之前会先问 / 非交互直接拒绝"
- [x] E2 README 的「不能做」清单同步
