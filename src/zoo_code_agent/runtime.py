"""运行期：按会话持有存储、账本与工具集；进程级单例由框架容器承载。

用 ``process_scoped`` 而不是模块级全局——后者正是框架自己在 issue #50 里要收编的
形态，示例不应把刚被清理掉的做法再示范一遍。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zoo_framework.core.container import ThreadSafety, process_scoped

from .model import ModelClient
from .session import SessionStore
from .tools import IdempotencyLedger, ToolRegistry, default_registry

LEDGER_KEY = "ledger"
"""幂等账本在会话状态作用域内的键名。"""


class SessionState:
    """一次会话的存储、幂等账本与工具集。

    账本与工具集**按会话**而非按进程：付费副作用的幂等性属于会话，跨会话共享账本会让
    不同会话的同名查询互相顶掉。账本随会话状态落盘，故重启续跑后既不重复扣费，
    也不丢掉既有条目。
    """

    def __init__(self, session_id: str, root: Path) -> None:
        self.session_id = session_id
        self.store = SessionStore(session_id)
        self.ledger = IdempotencyLedger(on_change=self._persist_ledger)
        self.ledger.restore(self.store.get(LEDGER_KEY))
        self.registry: ToolRegistry = default_registry(root, self.ledger)

    def _persist_ledger(self, snapshot: dict[str, Any]) -> None:
        self.store.set(LEDGER_KEY, snapshot)


@process_scoped(thread_safety=ThreadSafety.INSTANCE_GUARANTEED)
class AgentRuntime:
    """进程级运行期。

    Attributes:
        root: 只读工具的根目录。
        model: 模型客户端；``None`` 表示尚未配置。
        budget: token 预算上限。
        deadline: 截止期（``time.monotonic()`` 基准）；``None`` 表示不设。
    """

    def __init__(self) -> None:
        self.root: Path = Path.cwd()
        self.model: ModelClient | None = None
        self.budget: int = 0
        self.deadline: float | None = None
        self.sessions: dict[str, SessionState] = {}

    def configure(
        self,
        *,
        model: ModelClient,
        root: Path | str | None = None,
        budget: int = 1000,
        deadline: float | None = None,
    ) -> None:
        """装配运行期。

        Raises:
            RuntimeError: 重复配置。运行期是进程级单例，重复配置会让其中一次静默失效。
        """
        if self.model is not None:
            raise RuntimeError("运行期已配置，MUST NOT 重复配置；如需重来请先 reset()")
        self.model = model
        if root is not None:
            self.root = Path(root)
        self.budget = budget
        self.deadline = deadline

    def session(self, session_id: str) -> SessionState:
        """取（或建立）一个会话。

        重启续跑就发生在这里：作用域里已有的记录与账本会被读回。
        """
        state = self.sessions.get(session_id)
        if state is None:
            state = SessionState(session_id, self.root)
            self.sessions[session_id] = state
        return state

    def reset(self) -> None:
        """清空运行期状态。

        测试之间 MUST 调用——否则前一个用例的账本与会话残留会让后续断言在错误实现下
        依旧通过。注意它不清状态机里的落盘数据，那由 ``reset_all()`` 承担。
        """
        self.model = None
        self.sessions.clear()


def runtime() -> AgentRuntime:
    """取进程级运行期。"""
    return AgentRuntime()


def reset_all() -> None:
    """测试期完整复位：进程级载体 + 落盘存档。

    0.12.0 起框架自带 ``core.process_state.reset_process_state()``（变更
    ``declare-debt-carriers`` / #50），容器、配置面与适配器由它统一复位；本函数只补
    框架**刻意不复位**的载体，以及 0.8.0 时代不需要、0.12.0 之后必须做的一步。
    """
    reset_process_state_carriers()
    clear_persisted_state()


def reset_process_state_carriers() -> None:
    """复位框架的进程级共享载体（不动落盘存档）。

    ``reset_process_state()`` 覆盖容器与配置面；另有三处被框架声明为「不随用例复位」
    （``reset=None``），必须在这里补：两个挂在**类**上的注册表与**不在容器内**的
    ``WorkerRegistry`` 单例。``WorkerRegistry`` 尤其阴——它按**名字**缓存已实例化的
    Worker，于是第二个用例注册同名 Worker 时拿回的仍是第一个用例的实例（闭包里绑着
    第一个用例的会话），表现为「新会话永远不推进」。

    与 ``reset_all()`` 分开，是因为「模拟进程重启」的用例需要**保留落盘存档**，
    只丢弃内存载体。
    """
    from zoo_framework.core.process_state import reset_process_state
    from zoo_framework.core.worker_registry import get_worker_registry
    from zoo_framework.event.event_channel_register import EventChannelRegister
    from zoo_framework.reactor.event_reactor_manager import EventReactorManager

    reset_process_state()

    EventChannelRegister._channel_map.clear()
    EventReactorManager.reactor_map.clear()

    registry = get_worker_registry()
    registry._worker_instances.clear()
    registry._worker_classes.clear()
    registry._worker_factories.clear()
    registry._worker_metadata.clear()


def clear_persisted_state() -> None:
    """删除落盘的状态存档（含临时文件）。

    0.8.0 的读盘是空操作，所以旧测试从不关闭这条通道；0.12.0 修好之后（#72），
    上一次运行留下的 ``.zoo/zooStates.pic`` 会在 ``StateMachineWorker`` 首次执行时被
    **整表读回**，跨用例污染会话——实测表现是端到端用例里出现重复的 tool 记录。
    状态存档属于运行期产物，测试期必须清掉。
    """
    from zoo_framework.params import StateMachineParams

    path = Path(StateMachineParams.PICKLE_PATH)
    for candidate in (path, path.with_name(f"{path.name}.tmp")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
