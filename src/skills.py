"""技能：按需加载的指令包（渐进披露，issue #20）。

约定 ``<root>/skills/<name>/SKILL.md``，文件开头可选 frontmatter：

.. code-block:: markdown

    ---
    name: migration-check
    description: 改数据库 schema 时，先核对迁移脚本再动 ORM 模型
    ---
    （正文……）

系统提示里**只列名字 + 一行描述**；正文由 ``read_skill(name)`` 工具按需读入，作为一条工具
观察进入会话。这样"同一套流程"不必每次复述，也不会把所有细节一次性塞进提示。

frontmatter 只解析 ``key: value`` 行——够用且不引入 YAML 依赖；缺 frontmatter 时名字回落到
目录名、描述回落到正文第一行。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILLS_DIRNAME = "skills"
"""技能目录名（相对工作目录）。"""

SKILL_FILENAME = "SKILL.md"
"""技能正文文件名。"""

MAX_DESCRIPTION_CHARS = 200
"""一行描述的长度上限，避免它把系统提示挤掉。"""


@dataclass(frozen=True)
class Skill:
    """一份被发现的技能。

    Attributes:
        name: 技能名（frontmatter 优先，否则目录名）——``read_skill`` 用它取正文。
        description: 一行描述，进系统提示。
        path: 正文文件的来源路径，用于排错。
        body: 正文（不含 frontmatter）——**只在 ``read_skill`` 被调用时才进会话**。
    """

    name: str
    description: str
    path: str
    body: str


def load_skills(root: Path | str) -> tuple[Skill, ...]:
    """扫描 ``<root>/skills/*/SKILL.md``，按技能名排序返回。

    每次调用都重新扫描：技能是**文件**，改了就该下次生效，不需要缓存失效逻辑。
    目录不存在时返回空元组（没有技能是合法状态，不是错误）。
    """
    base = Path(root) / SKILLS_DIRNAME
    if not base.is_dir():
        return ()

    skills: list[Skill] = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir():
            continue
        file = directory / SKILL_FILENAME
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        meta, body = _split_frontmatter(text)
        name = str(meta.get("name") or directory.name).strip()
        description = str(meta.get("description") or _first_line(body)).strip()
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS] + "…"
        skills.append(Skill(name=name, description=description, path=str(file), body=body.strip()))
    return tuple(sorted(skills, key=lambda skill: skill.name))


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """拆出可选的 frontmatter，返回 ``(元数据, 正文)``。

    只认开头是 ``---`` 且后面有闭合 ``---`` 的形态；没有闭合就不当作 frontmatter——
    否则一份以 ``---`` 分隔线开头的普通文档会被误读掉正文。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            meta: dict[str, str] = {}
            for raw in lines[1:index]:
                key, sep, value = raw.partition(":")
                if sep and key.strip():
                    meta[key.strip()] = value.strip()
            return meta, "\n".join(lines[index + 1 :])
    return {}, text


def _first_line(body: str) -> str:
    """正文里第一条非空行，用于描述回落。"""
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""
