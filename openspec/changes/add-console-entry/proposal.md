## Why

`adopt-scaffold-layout` 把交付形态定为"应用，不是分发包"——`[project.scripts]` 被移除，
运行方式只有「进目录跑 `python src/main.py`」。这个决定对"标准脚手架项目"是对的，
但代价在**试用成本**上暴露出来：

```
$ uv tool install git+https://github.com/YearsAlso/zoo-code-agent
Installed 5 packages
 + zoo-code-agent==0.0.1 (from git+...)      ← 装上了，却没有任何可执行命令

$ uvx --from git+https://github.com/YearsAlso/zoo-code-agent zoo-code-agent
Package `zoo-code-agent` does not provide any executables.
```

想试一次的人得先 clone、建虚拟环境、装依赖、记路径参数。这个仓库的主要用途是**演示**，
而演示的门槛直接决定有没有人看。

## What Changes

**A 组 · 一个控制台入口**

- `pyproject.toml` 增加 `[project.scripts]`：`zca = "src.main:main"`（**一个**，
  指向与就地运行同一个 `main`，不复制第二份参数解析）
- 之后 `uvx --from git+https://github.com/YearsAlso/zoo-code-agent zca --help` 可直接运行

**B 组 · 让平铺导入在安装态也能解析**

- 引擎模块是平铺的顶层模块（`from agent import ...`），就地运行靠 `sys.path[0] = src/` 解析；
  被当作包安装后**解析不了**——实测 `import src.cli` 直接
  `ModuleNotFoundError: No module named 'agent'`
- 因此 `src/__init__.py` 把自身目录放上 `sys.path`：导入 `src.main`（入口目标）必然先执行它，
  就地运行时是空操作
- 入口因此指向 `src.main:main` 而不是 `src.cli:main`

**C 组 · 规格修订**

`project-layout` 的「交付形态 MUST NOT 是分发包」被**移除**（它明文禁止控制台入口），
替换为「交付形态 MUST 同时支持就地运行与安装后调用」。语义没有放宽成"可以随便发"：
仍然 SHALL NOT 承诺公共 API 与版本兼容，仍然只允许**一个**入口，且必须与就地运行共用同一份实现。

**D 组 · 让"装上后第一条命令"不踩坑**

把入口接到 `uvx` 之后，实测暴露两个**只在安装态出现**的问题，一并修掉：

1. **基准依赖 pytest，缺它时的报错看不懂**：默认安装不含 pytest，子进程以
   `No module named pytest` 退出码 1，而表格只显示"验收命令退出码 1"。现在提前拦下来，
   给出一句可照做的提示，并新增 `eval` extra（`pip install 'zoo-code-agent[eval]'`）。
2. **在项目目录外跑基准慢 10 倍**：框架读 cwd 下的 `./config.json`，任意目录里没有它，
   于是回落到默认（推模型关、`event:delay` 5 秒）——实测同一批任务从 2.4 秒变成 20.5 秒。
   基准现在显式设定节拍（与本仓库 `config.json` 一致，做法同 `bench/measure_session_latency.py`），
   使数字与所在目录无关。

## Capabilities

### Modified Capabilities

- `project-layout`：交付形态从"不做分发包"改为"就地运行 + 一个安装入口"，并新增
  "平铺导入在安装态可解析"的场景

## Impact

- `pyproject.toml`（新增 `[project.scripts]`）、`src/__init__.py`（路径桥）、`src/cli.py`（`prog` 改名）
- `tests/test_layout.py` 的守卫从"不许有入口"改为"入口必须恰好是 `src.main:main` 且桥存在"
- README 增加 `uvx` 一行；CI 不变（`pip install -e ".[dev]"` 会顺带生成命令）
- 不修改 zoo-framework

## Non-goals

- 不做 npm 包（那需要 Node 层 shim + 仍要依赖本入口，收益不抵维护面）
- 不恢复完整的"可发布库"承诺：`src/` 仍是项目根，`python src/main.py` 仍是基本方式，
  安装入口只是**多一条进入路径**
