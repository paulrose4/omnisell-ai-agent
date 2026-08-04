# ADR-004：Amazon Pilot 保持只读，写入仅在 Mock 人工执行中验证

Status: Accepted for current release

Date: 2026-08-05

## Context

OmniSell 已具备 Amazon OAuth、同步、运营异常、审批和 Mock Seller Central 执行能力，但尚未获得真实 Amazon 写入、真实运营 SOP、长期稳定性、具名采纳率和生产告警的证据。直接开放真实写入会把模型质量、审批完整性、adapter 语义、重放、限流和恢复风险叠加到真实店铺。

项目面试价值来自安全边界和工程化，而不是通过过早开放写入证明“更像生产”。真实 Amazon 只读数据也必须与 Mock、CSV、估算和 imported data 清晰区分。

## Decision

当前候选版本采用 read-only Amazon Pilot：

- AMAZON_WRITES_ENABLED 必须为 false。
- AMAZON_PILOT_READ_ONLY 必须为 true。
- 真实 Amazon adapter 只允许读取和同步。
- Refund、payment reversal、autonomous cancellation 和跨租户操作永久阻断。
- 所有写入流程只在 Mock Seller Central 中以人工执行方式验证，但使用与未来真实 adapter 相同的 approval contract、idempotency、action hash、audit 和 recovery 规则。
- UI 和文档必须标记数据来源，Mock 或 imported data 不能显示为 verified Amazon data。

## Alternatives

1. 开放低风险真实写入，例如小幅价格更新。拒绝原因：真实 provider、审批和 adapter 的联合证据不足，且业务损失不可逆。
2. 只使用 Mock，不接真实 Amazon。拒绝原因：无法验证 OAuth、限流、数据映射、新鲜度和真实只读数据质量。
3. 允许 Operator 在 UI 中自由填写写入参数。拒绝原因：破坏 approved plan 与实际执行绑定。
4. 让审批后自动写入。拒绝原因：当前产品承诺是安全型只读控制塔，自动执行需要单独 Pilot、Threat Model 和运营接受。

## Consequences

正面结果：

- 将真实业务风险限制在读取和建议，不会对店铺产生写入副作用。
- 可以先收集 detection、citation、latency、false positive 和 operator adoption。
- Mock 流程可继续验证完整审批协议，而不降低安全规则。
- 面试表述清晰，不会把实验能力夸大为生产上线。

代价：

- 无法证明真实写入成功率、Amazon 写入限流、最终一致性和回滚。
- 业务收益只能通过只读 Pilot 的节省时间和决策质量间接评估。
- Operator 仍需在真实 Seller Central 手工执行。

退出条件：

- 真实模型批次达到既定 quality/latency/cost 门禁。
- PostgreSQL 非 owner RLS、Compose/Playwright 黄金闭环和并发执行通过。
- 真实 Amazon read-only Pilot 连续运行 2 至 4 周，具名运营人员完成风险审查。
- 每一种真实写入都有 adapter-specific policy、idempotency、action hash、recovery、audit 和 rollback。
- 新 Threat Model 与 ADR 获得 Owner、Security 和 Operations 签字。
