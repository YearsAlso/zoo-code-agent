# Tasks — add-task-eval

## A · 任务集

- [x] A1 `bugfix-01`：改掉写错的运算符
- [x] A2 `feature-01`：把 `NotImplementedError` 桩补成实现
- [x] A3 `edgecase-01`：修空输入下的边界行为
- [x] A4 每个任务声明：样例文件、目标、脚本化计划、允许的工具集合
- [x] A5 未知任务标识报错并列出可用标识（不静默返回）

## B · 独立判定

- [x] B1 沙箱铺设 + 独立执行验收命令，判定只看退出码
- [x] B2 考题文件内容哈希前后比对，改测试即判失败
- [x] B3 验收超时与 agent 超时分别记录为失败原因

## C · 指标与落盘

- [x] C1 记录轮数、工具调用数、tokens、墙钟
- [x] C2 汇总取中位数，失败原因逐条列出
- [x] C3 结果写 JSON；CLI 打印 ASCII 表（避免控制台编码假失败）
- [x] C4 `python src/main.py --eval [--task ID] [--repeats N] [--real-provider]`

## D · 配置真相

- [x] D1 `config.json`：`event:pushModelEnabled=true` 且 `event:delay` 调小
- [x] D2 配置守卫用例，防止节拍被静默改回默认 5 秒

## E · 验证

- [x] E1 三个任务的脚本化计划全部通过
- [x] E2 负向：什么都不做的模型必须判失败
- [x] E3 负向：改写测试文件必须被判失败并指出被改的文件
- [x] E4 全量用例通过；`ruff check` 通过
- [x] E5 `openspec validate add-task-eval --strict` 通过
