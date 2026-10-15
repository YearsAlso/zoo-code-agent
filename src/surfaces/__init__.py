"""界面层：命令行、交互外壳、渲染、全屏界面、RPC、嵌入式 SDK。

取名 `surfaces` 而不是 `ui`：`rpc` 与 `sdk` 不是界面——它们与 CLI 共享的是"对外的面"，
而不是"画在屏幕上的东西"。这一层可以依赖下面任何一层，反过来不允许
（`tests/test_architecture.py` 守着方向）。

**包内仍然分层**：`render` / `commands` 在第 1 层，`shell` 在第 4 层，
`rpc` / `sdk` / `tui_app` 在第 5 层，`cli` / `tui` 在第 6 层。
收进一个包不是把层号压平——方向约束照旧。

包内各模块以包路径互导（`from surfaces.cli import ...`），MUST NOT 用相对导入。
"""
