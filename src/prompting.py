"""输入层：把"从哪读一行"抽成一个可替换的契约。

**为什么非抽不可**：原来的 ``repl()`` 直接用 ``stdin.readline()``。它有一个看得见的好处
（测试可以注入假 stdin）和一个看不见的代价：``readline`` 只在 ``input()`` 那条路上生效，
所以 ``stdin.readline()`` **绕过**了 GNU readline——方向键、Ctrl-A、历史、补全全都没有
（实测：``src/`` 下没有任何 readline / prompt_toolkit 引用）。

这里的做法是"两条路都留着"：真实终端走 ``input()``（拿到 stdlib readline 的历史与编辑能力），
其余情况走注入的流。对调用方来说都是 ``LineSource``，外壳不必知道区别。

行尾 ``\\`` 续行也在这里处理：粘贴多行提示时，"每行各起一轮"是最容易被吐槽的行为之一。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TextIO

HISTORY_ENV = "ZCA_HISTORY"
"""历史文件路径的覆盖开关。"""

DEFAULT_HISTORY = Path(".zoo") / "history"
"""默认历史文件：跟会话存档放一起，都在 ``.zoo/`` 下（该目录已被 git 忽略）。"""

CONTINUATION = "\\"
"""续行标记。选它是因为它是 shell 里既有的约定，不需要额外解释。"""


class LineSource:
    """一行输入的来源。"""

    is_terminal: bool = False

    def read(self, prompt: str = "") -> str | None:
        """读一行；返回 ``None`` 表示输入结束（EOF）。"""
        raise NotImplementedError

    def close(self) -> None:
        """收尾（写历史文件等）。"""


class StreamLineSource(LineSource):
    """从给定的流逐行读——管道、CI、测试替身都走这条路。

    Args:
        stream: 输入流。
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self.is_terminal = bool(getattr(stream, "isatty", lambda: False)())

    def read(self, prompt: str = "") -> str | None:
        line = self._stream.readline()
        return None if line == "" else line.rstrip("\n")


class TerminalLineSource(LineSource):
    """真实终端：``input()`` + stdlib readline。

    给三件东西：跨会话历史（落 ``.zoo/history``）、行编辑（方向键 / Ctrl-A / Ctrl-E）、
    补全（斜杠命令与 ``@`` 路径）。全部来自标准库——**不引入新依赖**是硬要求，
    否则"零依赖可跑通"这条保证会变成"装了才能跑"。

    Args:
        history_path: 历史文件位置。
        completions: 需要补全的静态候选（斜杠命令）。
        root: 补全 ``@`` 路径时的根目录。
        input_fn: 读一行的函数，默认内建 ``input``。测试可以塞替身。

    Raises:
        ImportError: 该平台没有 ``readline``（Windows 上 Python 自带，一般不会缺）。
    """

    is_terminal = True

    def __init__(
        self,
        *,
        history_path: Path | None = None,
        completions: Iterable[str] = (),
        root: Path | str = ".",
        input_fn=None,
    ) -> None:
        import readline

        self._readline = readline
        self._input = input_fn or input
        self._root = Path(root)
        self._completions = tuple(completions)
        self._history_path = Path(os.environ.get(HISTORY_ENV) or (history_path or DEFAULT_HISTORY))
        self._loaded = 0
        self._matches: list[str] = []
        self._setup_history()
        self._setup_completion()

    def _setup_history(self) -> None:
        """读旧历史、设定增长上限。父目录不存在时吃掉异常——历史写不下去不该让外壳起不来。"""
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            if self._history_path.is_file():
                self._readline.read_history_file(str(self._history_path))
            self._loaded = self._readline.get_current_history_length()
        except OSError:
            pass
        self._readline.set_history_length(1000)

    def _setup_completion(self) -> None:
        """补全：``/`` 开头补命令，``@`` 开头补路径。"""
        self._readline.set_completer(self._complete)
        self._readline.parse_and_bind("tab: complete")

    def _complete(self, text: str, state: int) -> str | None:
        """补全器：``state`` 为 0 时算出一批候选，之后按序号取。"""
        if state == 0:
            self._matches = self._candidates(text)
        if state < len(self._matches):
            return self._matches[state]
        return None

    def _candidates(self, text: str) -> list[str]:
        """按前缀给出候选：``/`` 补命令，``@`` 补路径。"""
        if text.startswith("/"):
            return [name for name in self._completions if name.startswith(text)]
        if text.startswith("@"):
            return self._path_candidates(text[1:])
        return []

    def _path_candidates(self, raw: str) -> list[str]:
        """把 ``@`` 后面的部分当路径前缀，列出根目录里匹配的条目。"""
        base = Path(raw).parent if raw else Path()
        try:
            entries = sorted((self._root / base).iterdir())
        except OSError:
            return []
        prefix = Path(raw).name
        return [
            f"@{(base / entry.name).as_posix()}{'/' if entry.is_dir() else ''}"
            for entry in entries
            if entry.name.startswith(prefix)
        ]

    def read(self, prompt: str = "") -> str | None:
        try:
            return self._input(prompt)
        except EOFError:
            return None
        except KeyboardInterrupt:
            # 在提示符上按 Ctrl-C：不是"打断一轮"，而是"这一行不要了"。
            print()
            return ""

    def close(self) -> None:
        """把本次新增的历史追加进文件。"""
        try:
            current = self._readline.get_current_history_length()
            if current > self._loaded:
                self._readline.append_history_file(current - self._loaded, str(self._history_path))
        except (OSError, AttributeError):
            pass


def read_prompt(source: LineSource, prompt: str) -> str | None:
    """读一条完整提示，处理续行。

    Returns:
        拼好的提示；``None`` 表示输入结束。**空串**表示使用者这次只是按了回车。
    """
    first = source.read(prompt)
    if first is None:
        return None
    pieces = [first]
    while pieces[-1].rstrip().endswith(CONTINUATION) and len(pieces) < 200:
        pieces[-1] = pieces[-1].rstrip()[: -len(CONTINUATION)]
        nxt = source.read("... ")
        if nxt is None:
            break
        pieces.append(nxt)
    return "\n".join(pieces)


def iter_prompts(source: LineSource, prompt: str = "> ") -> Iterator[str]:
    """把行来源变成提示序列（给批量驱动用）。"""
    while True:
        item = read_prompt(source, prompt)
        if item is None:
            return
        yield item
