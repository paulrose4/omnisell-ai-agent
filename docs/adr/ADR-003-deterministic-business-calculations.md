# ADR-003：确定性代码拥有业务计算与状态事实

Status: Accepted

Date: 2026-08-05

## Context

Amazon 运营涉及金额、费用、利润率、库存数量、补货量、价格底线、权限、审批和状态迁移。这些结果会直接影响经营决策和潜在 side effect。LLM 擅长解释和组织非结构化信息，但不能稳定保证 Decimal 精度、币种一致性、边界条件、时间新鲜度和可重复结果。

如果让模型在自然语言中重新计算利润或补货量，即使大多数案例正确，也无法给出足够强的审计证据。模型还可能引入 evidence 中不存在的数字，或者在 Schema repair 时改变金额。

## Decision

确定性代码是以下事实的唯一来源：

- Money 使用 Decimal 和 ISO 4217 currency。
- 时间使用 timezone-aware UTC。
- inventory health、days of supply、reorder point、recommended reorder quantity。
- revenue、fees、landed cost、contribution profit、margin、break-even price。
- pricing floor、stockout guardrail、policy decision 和 risk level。
- tenant authorization、approval count、action hash、idempotency 和 workflow state。

Agent 只能引用这些结果并输出 explanation、hypothesis、missing data、confidence 和 bounded draft。EvidenceReference 可包含 deterministic_values；EvidenceValidator 会拒绝模型新增、冲突或修改的金额、库存和其他标记字段。

## Alternatives

1. 让 LLM 直接读取原始订单并计算利润。拒绝原因：精度、可重复性、币种、缺失输入和审计不可控。
2. 让 LLM 给出补货量，代码只做范围裁剪。拒绝原因：裁剪不能证明原始逻辑正确，也会隐藏不支持的数值来源。
3. 把所有规则写入 Prompt。拒绝原因：Prompt 不是可执行 policy，版本差异和 provider 行为会改变结果。
4. 使用 float 以简化实现。拒绝原因：金额舍入和 hash 稳定性无法保证。

## Consequences

正面结果：

- 相同输入得到相同结果，易于回放、测试和解释。
- 金额、库存和 policy 可达到高分支覆盖并独立审查。
- Agent 失败或 provider 不可用时，核心业务事实仍然存在。
- evidence validator 可以把 hallucination 转化为明确拒绝。

代价：

- 每个业务规则需要领域模型、版本、输入清单、缺失数据策略和迁移。
- 模型不能灵活“修正”业务结果，任何规则变化必须走代码审查。
- 数据质量不足时系统会降级或要求人工审批，而不是给出看似完整的答案。

后续约束：

- 新 persistent business model 必须有 Alembic、tenant index、unique/check constraints 和 PostgreSQL RLS。
- 新数值字段必须声明币种、单位、精度、舍入和时间语义。
- Eval 必须分别统计 unsupported deterministic claim 与 unsupported advisory claim。
- 任何允许模型改变 deterministic value 的需求都需要新的 ADR 和安全审查。
