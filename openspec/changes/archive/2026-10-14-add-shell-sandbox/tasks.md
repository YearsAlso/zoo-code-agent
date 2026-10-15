# Tasks — add-shell-sandbox

## A · 策略与探测

- [x] A1 `macos_profile(root, *, allow_network)`：纯函数，产出 SBPL 文本
- [x] A2 `bwrap_prefix(root, *, allow_network)`：纯函数，产出 argv 前缀
- [x] A3 `wrap(command, boundary)`：把命令包进后端
- [x] A4 `probe()`：**真跑一次**最小命令；`which` 命中但跑不通 → 不可用（本机就是这种情况）
- [x] A5 `resolve(mode)`：auto / strict / none 三档，strict 无边界即报错

## B · 边界说明

- [x] B1 `Boundary`：后端名、写范围、网络、**挡不住什么**（没有边界时那句"授权不是隔离"）
- [x] B2 横幅、`/status` 与每场会话开头的一行都显示当前边界

## C · 接线

- [x] C1 `run_shell` 按边界包装；无边界时行为与今天一致（`/bin/sh -c` 显式写出来）
- [x] C2 命令失败且边界生效时，观察里说清"若与写文件有关，当前边界是……"
- [x] C3 `--sandbox auto|strict|none` + `--sandbox-no-network`

## D · 验证

- [x] D1 `tests/test_sandbox.py`（20 条）：两条纯策略、探测（含 which 命中但跑不通）、三档、包装
- [x] D2 降级路径的行为：本机无可用边界时如实报告，且命令照常能跑
- [x] D3 真隔离用例：**有可用边界时才跑**（本机跳过并写明"未测量"，换台机器即可坐实）
- [x] D4 全量 347 条用例通过（1 条按 D3 跳过）；`ruff check` / `ruff format --check` 通过
- [x] D5 两份 README 把"没有沙箱"改成三档对照；ROADMAP #42 移出 Next；数字更新
- [x] D6 `openspec validate add-shell-sandbox --strict` 通过

## 未测量（如实记录）

- **真实隔离**：本机 `/usr/bin/sandbox-exec` 存在但套用被拒（嵌套沙箱环境），`bwrap` 未安装。
  两条后端的**代码路径**都已实现并有用例覆盖（策略文本、argv 形状、包装、降级说话），
  但"越界写被真的挡住"这件事**在这台机器上未测量**。那条用例带明确原因跳过。
- **断网**：同理未测量（本机网络本来就被宿主限制，测出来的"通不过"证明不了是沙箱拦的）。

## E · 演示时当场发现并修掉的一处名实不符

- [x] E1 `--no-banner` 只关掉了图标，信息块照打——开关的名字在说谎。现在它关掉**整块**横幅；
  `/logo` 仍然可以显式重印（那是使用者的主动要求，"启动时不要"不该拦着他）
- [x] E2 边界那一行改由外壳在**每次会话开头**打印，与横幅解耦：关掉横幅时它也必须看得见——
  "我批过这条命令"很容易被感受成"它被隔离了"，那正是这句话要纠正的错觉
