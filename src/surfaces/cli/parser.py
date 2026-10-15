"""命令行参数、默认值与退出码：**不认识模型、也不认识会话**，只把 argv 变成配置。

两个默认值（系统提示、会话名）也放这里：它们出现在参数的默认位置上，放在别处会让 `parser.py` 反向依赖装配层（切分时实测撞到过）。"""

from __future__ import annotations

import argparse
from pathlib import Path

from kernel.sandbox import MODE_AUTO, MODES
from providers.openai_compat import DEFAULT_BASE_URL
from providers.retry import DEFAULT_ATTEMPTS, DEFAULT_BASE_DELAY

EXIT_OK = 0


EXIT_BUDGET = 2


EXIT_DEADLINE = 3


EXIT_TIMEOUT = 4


EXIT_ERROR = 5


EXIT_CANCELLED = 6


STOP_TO_EXIT = {
    "model_stopped": EXIT_OK,
    "budget_exhausted": EXIT_BUDGET,
    "deadline_reached": EXIT_DEADLINE,
    "cancelled": EXIT_CANCELLED,
}
"""终止原因 → 退出码。未列出的原因按错误处理，避免"不认识就当作成功"。"""


DEFAULT_SYSTEM = (
    "你是一个在指定工作目录内工作的编码助手。先用工具看清现状，再动手改。"
    "只改源码，MUST NOT 通过修改测试文件来让测试通过。"
)


DEFAULT_SESSION = "cli"


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="zca",
        description="基于 zoo-framework 的最小 coding agent。",
        epilog="就地运行等价：python src/main.py（参数与行为完全一致）。",
    )
    parser.add_argument("-p", "--prompt", help="单次执行的提示；省略则进入交互式会话")
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="进入交互式会话（无参数时必需显式指定——不带参数的命令是脚手架默认行为）",
    )
    parser.add_argument("--root", default=".", help="工作目录，默认当前目录")
    parser.add_argument("--budget", type=int, default=200_000, help="token 预算上限")
    parser.add_argument("--timeout", type=float, default=300.0, help="单轮墙钟上限（秒）")
    parser.add_argument("--json", action="store_true", help="以 JSON 事件流输出（每行一个对象）")
    parser.add_argument(
        "--allow",
        default="",
        help="预授权的工具名，逗号分隔（如 write_file,edit_file,run_shell）；默认只读",
    )
    parser.add_argument(
        "--approve-all",
        action="store_true",
        help="放行一切需要审批的操作（不做逐次确认，仅限可信环境）",
    )
    parser.add_argument(
        "--script",
        type=Path,
        help="用脚本化模型跑（JSON 响应列表），不需要任何 API 密钥",
    )
    parser.add_argument("--model", default=None, help="真实提供方的模型 id")
    parser.add_argument(
        "--provider",
        choices=("anthropic", "openai"),
        default="anthropic",
        help=(
            "真实提供方：anthropic 或 openai（任何 OpenAI 兼容端点）。"
            "默认保持不变，既有用法不受影响"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"OpenAI 兼容端点的地址前缀（默认官方 {DEFAULT_BASE_URL}）",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_ATTEMPTS - 1,
        help=(
            f"失败后额外重试的次数（默认 {DEFAULT_ATTEMPTS - 1}；0 表示不重试）。"
            "只重试限流与临时故障——鉴权失败、参数错误不会白等"
        ),
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=DEFAULT_BASE_DELAY,
        help=f"首次重试前等待的秒数（默认 {DEFAULT_BASE_DELAY}），之后指数增长、有上限",
    )
    parser.add_argument("--session", default=DEFAULT_SESSION, help="会话标识")
    parser.add_argument(
        "--renderer",
        choices=("auto", "plain", "ansi", "rich"),
        default="auto",
        help="输出渲染器：auto 按终端能力挑；plain 与改造前逐字节一致；ansi 零依赖带颜色",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="颜色策略，默认 auto（非终端、NO_COLOR、TERM=dumb 时关闭）",
    )
    parser.add_argument(
        "--banner",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="启动横幅；默认仅在有终端时打印（--no-banner 关闭）",
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        help=(
            "批量任务文件（一行一个提示，# 开头为注释）；交给 Master 逐条派发。"
            "批量按无人值守处理：不逐次询问，未预授权的写 / 执行会被拒绝"
        ),
    )
    parser.add_argument("--concurrency", type=int, default=2, help="批量任务的并发上限")
    parser.add_argument(
        "--tool-module",
        action="append",
        default=[],
        metavar="模块名",
        help="加载额外工具的模块（暴露 TOOLS；可重复）。显式指定即视为已信任",
    )
    parser.add_argument(
        "--no-plugins",
        action="store_true",
        help="不加载已安装插件通过入口点声明的工具",
    )
    parser.add_argument(
        "--trust-project-tools",
        action="store_true",
        help=(
            f"信任工作目录下的项目级工具声明（{'.zoo/tools.py'}）。"
            "不给这个开关时，该文件存在即报错——它等于让工作目录里的代码在本进程里执行"
        ),
    )
    parser.add_argument(
        "--mcp",
        action="append",
        default=[],
        metavar="命令",
        help="接入一个 stdio MCP 服务器（可重复），例如 --mcp 'python my_server.py'",
    )
    parser.add_argument(
        "--mcp-timeout",
        type=float,
        default=30.0,
        help="单次 MCP 请求的墙钟上限（秒）。服务器不回答时按失败处理，不会挂住",
    )
    parser.add_argument(
        "--sandbox",
        choices=MODES,
        default=MODE_AUTO,
        help=(
            "执行命令的边界：auto 有可用后端就用（默认）；strict 必须有，没有就失败；"
            "none 明确不要。可用性以**实际试跑**为准，不是命令是否存在"
        ),
    )
    parser.add_argument(
        "--sandbox-no-network",
        action="store_true",
        help="边界内断网（默认允许联网）。需要网络的任务会失败，这是有意的",
    )
    parser.add_argument(
        "--show",
        metavar="会话:条目",
        default=None,
        help="打印某一条记录的原文然后退出（会话与条目地址，例如 cli:e3）",
    )
    parser.add_argument(
        "--fork",
        metavar="会话:条目",
        default=None,
        help=("从某一条记录分叉出一条新会话再跑；新会话标识取 --session。源会话一字不动"),
    )
    parser.add_argument(
        "--subagent",
        action="store_true",
        help=(
            "启用 subagent 工具：把子任务交给独立进程，只取回结论、证据与用量。"
            "默认关闭——它会起进程、会花 token。子进程走真实提供方"
            "（--script 的脚本化模型不传给它：父子共用一份脚本会互相抢游标）"
        ),
    )
    parser.add_argument(
        "--rpc",
        action="store_true",
        help=(
            "RPC 模式：常驻进程，按 JSONL 收发（一行一个 JSON 请求；事件行与 --json 同源）。"
            "方法：prompt / cancel / status / stop"
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="打印版本号然后退出（版本号只有一个真源：已安装分发包的元数据）",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help=(
            "全屏界面（需要装 extras：pip install 'zoo-code-agent[tui]'）。"
            "与逐行外壳共用同一套会话语义与审批策略，只换显示方式"
        ),
    )
    return parser
