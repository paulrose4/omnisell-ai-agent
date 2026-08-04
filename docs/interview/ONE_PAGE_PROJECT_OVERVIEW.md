# OmniSell 一页项目说明

## 项目定位

OmniSell 是面向 Amazon 运营团队的安全型只读运营控制塔，不是自由聊天产品。它将库存、利润、定价、异常、审批和人工执行组织成可审计工作流，让 LLM 负责解释证据和生成有界建议，让确定性代码负责金额、库存、权限、策略、审批和状态迁移。

## 业务问题与用户

目标用户是 Seller Operator、Approver、Owner 和只读 Reviewer。典型问题不是“让 Agent 自动经营店铺”，而是把缺货、利润恶化、对账差异和 Listing 风险从分散数据中及时发现，形成引用明确的建议，经过独立审批后由人工执行并重新同步验证。

传统 Dashboard 只能展示数据，自由 Agent 又容易越权和幻觉。OmniSell 的价值在于把 Agent 嵌入业务闭环，同时保留可回放、可拒绝、可恢复和可追责的控制面。

## 黄金闭环

1. 同步 Mock Amazon 数据并检测 SKU 缺货风险。
2. 确定性库存算法计算 days of supply、reorder point 和建议补货量。
3. Advisor 解释证据、置信度、缺失数据和新鲜度，不修改业务数值。
4. Operator 认领 Issue，系统创建绑定 tenant、connection、marketplace、Issue、Action、evidence、policy version 和 action hash 的审批请求。
5. 独立审批人完成审批；critical 动作要求两名不同审批人。
6. Mock Seller Central 只接受 approval_request_id 与 Idempotency-Key，从服务器端已批准计划重建执行命令。
7. 事件消费和重新同步后，系统用确定性规则确认 Issue resolved、Execution verified、Work Item completed。

## 架构边界

- Domain：Decimal 金额、ISO 4217、UTC 时间、库存、利润和定价规则。
- Agent Runtime：证据预算、上下文预算、Token/成本预算、超时、严格 Schema、一次 repair、降级记录和引用验证。
- Safety：Policy decision 与 risk level 分离，REQUIRE_APPROVAL 永远进入真实人工审批。
- Execution：action hash、不可变 approved plan、幂等、唯一执行权、失败恢复和审计。
- Data：所有业务查询 tenant scoped；持久模型要求 tenant index、约束、Alembic 和 PostgreSQL RLS。

## 为什么需要 Agent

确定性规则适合回答“库存是多少、利润是多少、是否允许执行”，但不擅长把跨域证据组织成可读解释、提出假设、指出缺失数据和生成内部任务草稿。Agent 只承担这些非确定性、可审查的工作。任何 side effect 都不能由模型自行决定或拼接业务参数。

## 当前量化证据

| 指标 | 当前结果 | 证据边界 |
| --- | ---: | --- |
| 后端全量测试 | 632 passed，8 skipped | 本地 SQLite 与 Mock；PostgreSQL 外部项跳过 |
| 总分支覆盖率 | 81.90% | 本地全量 coverage |
| 关键模块门禁 | 全部通过 | evidence、inventory、pricing、profitability、gateway contract、HITL、policy、workflow |
| Agent fixed-fixture | 12/12 structured success | 只证明评测器与契约，不证明真实 provider |
| 禁止动作拒绝 | 4/4 | fixed fixture，Wilson 区间样本仍小 |
| clean bootstrap | passed | Windows 中文路径、Python 3.12、hash lock |

真实模型曾出现四个案例全部触发 Schema repair，延迟约 35.8 至 89.3 秒。因此当前 Agent 评测 release decision 明确为 no-go，必须重新完成真实 provider 批量评测后才能升级结论。

## Pilot 核心 KPI

- 检测到 Issue 的平均时间和漏检率。
- Advisor 引用准确率、unsupported claim rate、首次 Schema 成功率、repair rate、P50/P95 延迟、Token 和成本。
- 审批等待时间、审批篡改拒绝率、幂等重放成功率。
- Issue resolution time、人工采纳率、编辑率和 false positive rate。

这些业务 KPI 目前没有真实运营数据，必须通过 2 至 4 周具名只读 Pilot 收集。

## 面试结论

该项目适合用于 Agent Runtime、AI 应用架构、LLM 工程化、AI 后端和安全智能体岗位。最强卖点不是功能数量，而是清晰回答三个问题：模型能做什么、模型不能做什么、系统如何证明它没有越界。
