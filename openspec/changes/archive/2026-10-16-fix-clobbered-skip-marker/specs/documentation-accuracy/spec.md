## ADDED Requirements

### Requirement: 测试模块 MUST NOT 多次赋值 `pytestmark`

一个测试模块 SHALL 只赋值 `pytestmark` 一次。同一模块需要多条跳过条件时，
MUST 写成一个列表（`pytestmark = [mark_a, mark_b]`）。

理由不是风格：后一次赋值会**静默**覆盖前一次。被覆盖的若是"缺可选 extras 就整组跳过"
这一条，后果是那组用例在缺依赖的环境里照跑、整组报错——而**本地可能完全看不出来**
（本机恰好装了那个依赖，或另有别的标记让它跳过）。实测过：`tests/test_anthropic_http.py`
里新加的 SDK 跳过标记被原有的回环端口标记覆盖，CI 六个默认作业全红。

这条约束 SHALL 由一条机械检查守住，检查 MUST 指出违规的文件与行号。

#### Scenario: 两次赋值被拒
- **WHEN** 某个测试模块在模块级两次赋值 `pytestmark`
- **THEN** 检查失败，并指出文件与第二次赋值的位置

#### Scenario: 列表形式被允许
- **WHEN** 某个测试模块用列表一次给出多条跳过条件
- **THEN** 检查通过——两条标记都生效，各自的跳过理由都保留

#### Scenario: 只赋值一次照旧
- **WHEN** 某个测试模块只赋值一次 `pytestmark`（无论是不是列表）
- **THEN** 检查通过
