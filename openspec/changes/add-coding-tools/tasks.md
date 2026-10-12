# Tasks — add-coding-tools

## A · 只读工具补齐

- [x] A1 `grep`：按正则逐行检索，返回「文件:行号: 内容」，有结果上限
- [x] A2 `find`：按 glob 匹配路径，有结果上限
- [x] A3 两者跳过 `.git` / 虚拟环境 / 缓存 / 运行期产物目录
- [x] A4 无效正则作为工具错误返回，而不是抛未捕获异常

## B · 副作用工具

- [x] B1 `write_file`：创建 / 覆盖，自动建父目录
- [x] B2 `edit_file`：唯一匹配才替换；未匹配、多次匹配均报错；`replace_all` 需显式
- [x] B3 `run_shell`：返回 exit_code 与 stdout/stderr；超时与超长输出不抛异常
- [x] B4 `run_shell` 的 `cwd` 固定为声明的根目录
- [x] B5 `paid_lookup` 升级为需要审批

## C · 权限门

- [x] C1 `Tool.requires_approval` 声明；写 / 执行 / 付费一律为真
- [x] C2 `PermissionPolicy.assess`：预授权 → 审批者 → 默认拒绝
- [x] C3 审批者抛错按拒绝处理（失败关闭）
- [x] C4 拒绝写成工具观察回灌，会话继续
- [x] C5 路径边界对所有工具生效（含符号链接越界）
- [x] C6 工具集 MUST NOT 含 `eval` / `exec`

## D · 装配

- [x] D1 `AgentRuntime.configure(policy=...)`；`build_master(policy=...)` 透传
- [x] D2 默认策略为只读；运行期策略是会话策略的真源（含 configure 之前已建的会话）

## E · 验证

- [x] E1 `tests/test_write_tools.py`（16 条）：写 / 编辑 / 执行 / 检索与边界
- [x] E2 `tests/test_permissions.py`（8 条）：失败关闭、拒绝回灌、无副作用
- [x] E3 `tests/test_coding_loop.py`：端到端「跑测试失败 → 读码 → 改码 → 跑测试通过」
- [x] E4 既有 C5 断言改写为「写/执行必须过门，代码求值不允许」
- [x] E5 全量 74 条用例通过；`ruff check` 通过
- [x] E6 `openspec validate add-coding-tools --strict` 通过
