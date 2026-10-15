## Why

README 里一直写着："**没有沙箱**——`run_shell` 的边界是工作目录 + 权限门，不是隔离"。
这句话是诚实的，但它也说明：把 agent 指向自己的仓库时，真正挡在"敢让它跑命令"前面的东西不存在。
权限门管的是"这条命令要不要执行"，**不管"执行之后能碰到什么"**——一条被批准的 `rm -rf ~` 照样跑。

ROADMAP 里这条（#42）的要求写得很克制：给一个**真实的**边界，README 写明它来自哪里，
拦不住的**如实记录**、不假装拦得住。这正是本变更的判据。

## What Changes

**A 组 · 边界（新增 `src/sandbox.py`）**

- 两档真实后端：macOS 的 `sandbox-exec`、Linux 的 `bwrap`（bubblewrap）
- **可用性探测是"真跑一次"**：`which` 命中不等于能用。本机实测就是反例——`sandbox-exec`
  存在，但套用被拒（`Operation not permitted`，嵌套沙箱环境）。只按 `which` 判断，
  会把一条命令交出去然后以一条看不懂的错误失败
- 策略纯函数：写只允许在根目录与临时目录内；严格档再断网络
- 三档开关：`auto`（有就用）/ `strict`（必须有，没有就**失败**）/ `none`（明确不要）

**B 组 · 如实说明边界在哪（`src/sandbox.py` / `src/render.py`）**

- `Boundary` 说清楚：用的是哪个后端、写能到哪、网络通不通、**挡不住什么**
- 终端横幅与 `/status` 显示当前边界。没有沙箱时明确写"边界是授权，不是隔离"——
  使用者不该以为"批过一次"等于"隔离了"

**C 组 · 接进工具（`src/tools.py`）**

- `run_shell` 在选了后端时用包装后的 argv 执行；没选时行为与今天一致
- 越界写失败时，观察里 MUST 说清"这是沙箱拦的"，而不是让模型以为是命令写错了

## Capabilities

### Added Capabilities

- `shell-sandbox`：后端探测（真跑一次）、策略、三档开关、边界说明

### Added Requirements（并入既有能力）

- `coding-tools`：`run_shell` SHALL 在有可用边界时在其内执行，且 SHALL 如实报告边界
- `cli-interface`：SHALL 提供沙箱开关，且 MUST NOT 假装"有沙箱"当它实际不可用

## Impact

- 新增 `src/sandbox.py`；`src/tools.py`（`run_shell` 包装）、`src/cli.py`（开关与探测）、
  `src/shell.py` + `src/render.py`（边界显示）
- `tests/test_sandbox.py`（新）
- 两份 README（把"没有沙箱"改成"边界来自哪里"的三档对照表）、ROADMAP
- 不新增依赖（两个后端都是系统自带的命令）

## Non-goals

- **不假装拦得住**：本机（嵌套沙箱）无法应用任何后端，所以这两条路**未测量**。
  探测通过才启用，探测不过就如实说"没有边界"——这比"配置里写了沙箱"更接近事实
- 不做容器 / 虚拟机级隔离：那是另一条产品线，且要求使用者装上别的东西
- 不做细粒度的域名白名单（网络只有"通"与"不通"两档）：中间态既难实现又容易给人虚假的安心
- 不改权限门：它依然是"要不要执行"的那道门，沙箱是它之后的第二层
