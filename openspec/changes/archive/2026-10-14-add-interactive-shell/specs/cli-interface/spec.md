## ADDED Requirements

### Requirement: 交互式入口 MUST 把「中断当轮」与「退出外壳」分开

交互式会话里，轮次进行中的中断 SHALL 只使**当前轮**以取消结束并回到提示符，MUST NOT 结束外壳；
外壳 SHALL 只由显式的退出命令或输入结束（EOF）结束。

#### Scenario: 中断不退出
- **WHEN** 在交互式会话里中断一个进行中的轮次
- **THEN** 回到提示符，外壳仍在运行

#### Scenario: 显式退出命令结束外壳
- **WHEN** 输入退出命令
- **THEN** 外壳结束，退出码为成功
