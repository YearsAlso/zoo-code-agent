# Tasks — add-interactive-shell

## A · 渲染层

- [x] A1 `RenderOp` 与 `render_ops(event)`：纯函数，输入事件、输出渲染指令
- [x] A2 `PlainRenderer`：与今天逐字节一致（golden 守住）
- [x] A3 `JsonRenderer`：每行一个 JSON 对象，与既有契约一致（golden 守住）
- [x] A4 `AnsiRenderer`：颜色、工具调用摘要、diff 着色、状态行、页脚
- [x] A5 `RichRenderer`：装了 `rich` 时可选用；缺失/构造失败时**静默回落** ANSI（同一批 ops 用例仍通过）
- [x] A6 颜色模式 `--color=auto|always|never` + `NO_COLOR` + 非 TTY 自动关闭
- [x] A7 `model_response` 事件带上工具参数——前端显示"改哪个文件"的唯一信息源就是它

## B · 输入层

- [x] B1 `LineSource` 契约与注入替身
- [x] B2 真 TTY 走 `input()` + stdlib `readline`：历史落 `.zoo/history`
- [x] B3 补全：`/命令` 与 `@路径`
- [x] B4 行尾 `\` 续行
- [x] B5 非 TTY / 注入时退化为逐行读，既有 REPL 用例不变
- [x] B6 「注入的假 TTY」MUST NOT 被当成真终端（否则 `input()` 去读真终端，命令挂住）

## C · 斜杠命令

- [x] C1 命令注册表与 `/help`
- [x] C2 `/exit` `/quit`；未知命令给出提示且 MUST NOT 退出
- [x] C3 `/cost` `/status`：真实 tokens / 预算 / 轮数 / 阶段
- [x] C4 `/new` `/sessions` `/resume <id>`
- [x] C5 `/allow <tool>`：本会话放行；`/skills`；`/logo`
- [x] C6 `/resume` 之后的第一条提示 MUST 被追加（否则静默丢失——实测踩到过）
- [x] C7 每次装配用**唯一** Worker 名：框架按名字缓存实例，同名会让新会话拿回旧闭包

## D · 中断语义

- [x] D1 `loop.resume_after_interrupt`：清终止态 + 让未派发的调用以"已取消"观察收尾
- [x] D2 Ctrl-C 打断当轮后回到提示符，会话仍可继续
- [x] D3 退出只有 `/exit` / `/quit` / Ctrl-D
- [x] D4 被打断的调用 MUST NOT 在下一轮被补跑（否则等于"说了停还偷偷干"）

## E · 阶段事件

- [x] E1 `agent_events.PHASE_TOPIC`
- [x] E2 `_set_phase` 在阶段真的变了时发布 `phase_changed`
- [x] E3 状态行消费该事件（不用轮询 store）
- [x] E4 终止阶段不单独占一行（紧随的 stop 事件已经说明）

## F · 本会话放行

- [x] F1 `PermissionPolicy.allow_tool(name)`
- [x] F2 审批界面按 `a` 放行该工具；此后再问同一工具 MUST NOT 再出现
- [x] F3 放行走回调而不是抓住策略对象（`/new` 换策略后仍要生效）

## G · 横幅

- [x] G1 mark 几何渲染（半块字符 + 每格两色，参数取自 `docs/assets/mark.svg`）
- [x] G2 墨色交给终端默认前景（`ink` 样式），只有强调色 `#33aa99` 上真彩 → 浅底深底都对
- [x] G3 编码不足时退成纯 ASCII 变体（`cp1252` 编不出 `▀`，实测过的坑）
- [x] G4 `--banner` / `--no-banner` 与 `/logo`

## H · 接线、测试与文档

- [x] H1 `-i` 转交 `shell.py`；`--json -i` 仍走 JSON 渲染器
- [x] H2 用例：ops 契约、两条 golden、输入替身、命令、中断后继续、阶段事件、放行、降级链
- [x] H3 用例：`--json` 的 stdout MUST NOT 混入横幅 / 状态行 / 颜色转义
- [x] H4 两份 README 增"交互式外壳"一节；ROADMAP 改掉"终端与 CLI 够用"并补"不做全屏 TUI"
- [x] H5 `pyproject.toml` 增 `tui` extra（仅 `rich`），默认安装不引入新依赖
- [x] H6 CI 增一个 `tui` 作业：装了 rich 之后**同一批用例**必须照样全绿
- [x] H7 全量 211 条用例通过（本机 Python 3.13）；`ruff check` / `ruff format --check` 通过
- [x] H8 `openspec validate add-interactive-shell --strict` 通过

## 实测到的三处坑（都在用例里留了守卫）

| 坑 | 症状 | 守卫 |
|---|---|---|
| 会话早于 `configure()` 建立 | 工具用 `Path.cwd()` 当根，**文件写到仓库根上**，命令输出看不出异常 | `Shell.__init__` 先 `runtime.set_root()`；用例断言写在 `tmp_path` 里 |
| 认假 TTY 为真终端 | 注入 `isatty()=True` 的替身会让 `input()` 去读真终端 → 命令挂住 | `build_line_source` 用 `stdin is sys.stdin` 判定 |
| 同名 Worker 复用实例 | 切会话后循环绑着旧会话，表现为"新会话不推进" | 每次装配 `worker_name=f"AgentLoop-{n}"` |
