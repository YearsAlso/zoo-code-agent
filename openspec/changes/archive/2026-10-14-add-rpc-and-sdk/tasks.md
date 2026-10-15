# Tasks — add-rpc-and-sdk

## A · 协议

- [x] A1 `parse_request(line)`：坏行给可操作的错误（含"可用方法"清单）
- [x] A2 `encode_response(id, result)` / `encode_error(id, message)`；事件行由 `JsonRenderer` 产出
- [x] A3 方法集：`prompt` / `cancel` / `status` / `stop`

## B · 服务端

- [x] B1 `serve(...)`：读一行 → 分派 → 交给既有的 `build_master` / `drive_with_renderer`
- [x] B2 事件行经 `JsonRenderer` 输出（与 `--json` **同一个渲染器**，同源因此是结构性的）
- [x] B3 会话语义复用既有实现：新会话起、既有会话追加、打断后可继续
- [x] B4 非交互失败关闭：需要审批的工具被拒（预授权仍有效）
- [x] B5 `stop` / EOF：收尾停机（不留后台 Worker）

## C · SDK

- [x] C1 `open_session(...)` 句柄：`send` / `cancel` / `status` / `close`（首轮由第一次 send 起）
- [x] C2 `run_session(...)` 一次性入口
- [x] C3 两个入口都不自带循环 / 事件总线 / 存储（模块短且只 import 既有组件）

## D · 入口

- [x] D1 `--rpc` 开关（`cli.py`，走 `cli.main` 的分流即可——`src/main.py` 已把参数都转给 cli）

## E · 验证

- [x] E1 `tests/test_rpc.py`（23 条）：协议、方法集、失败关闭、预授权、打断后继续、收尾、EOF
- [x] E2 **同源证明** ×2：同一条会话走 `--json` 与走 RPC / SDK，事件序列一致
- [x] E3 SDK 用例：句柄跑多轮、事件回调、状态、失败关闭、显式策略
- [x] E4 全量 404 条用例通过；`ruff check` / `ruff format --check` 通过
- [x] E5 两份 README 增 RPC/SDK 一节；ROADMAP #45 移出 Next；数字更新
- [x] E6 `openspec validate add-rpc-and-sdk --strict` 通过

## F · 顺手对齐的一处行为不一致

- [x] F1 RPC 里"轮次进行中取消"之后，**未派发的调用**原本会留到下一次 `prompt` 被补跑——
  外壳早就不会这么做了（`resume_after_interrupt` 让它以"已取消"收尾）。现在 RPC 走同一条路，
  并有用例断言"它只留下一条收尾观察、没有真的执行"。这条也说明：**多入口的价值之一，
  就是逼出这种"只有一处想清楚了"的偏差**。

## 未测量 / 明确不做

- 多会话并发、请求乱序、断线重连：都要一个真正的会话管理器，属后续
- 在 RPC 里做审批往返（"没有人可问"就是当前的设计）：`--allow` 预授权是唯一入口
- SDK 的版本兼容承诺：本仓库不承诺公共 API
- RPC/SDK 走**真实模型**的端到端：与 README 里其它地方一样，缺密钥，未测量
