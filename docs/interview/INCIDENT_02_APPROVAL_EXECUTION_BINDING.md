# 失败复盘 02：审批内容与实际执行未完全绑定

## 摘要

安全审计发现，审批请求会对 Issue、Recommended Action、evidence 和 proposed changes 计算 action hash，但旧 Mock Seller Central 执行接口仍接受客户端 action_type 和 quantity。接口只检查 Issue 已进入 approved_for_manual_execution，并未从已批准请求重建精确执行计划，因此理论上存在“批准 A、执行 B”或“批准数量 100、执行 101”的路径。

## 影响

- 审批完整性被破坏，action hash 不能证明实际 side effect 与批准内容一致。
- 客户端或被攻陷的 BFF 可以替换动作类型、数量或连接。
- 重放和并发执行可能对同一审批产生多个 side effect。
- 即使当前目标是 Mock，人为保留更弱的 Mock 协议也会让未来真实 Amazon adapter 继承错误边界。

## 发现方式

在审查 approval creation、workflow resume 和 /api/mock-seller-central/execute 时，对照仓库规则逐项检查 authorization、policy、idempotency、action hash、audit 和 recovery。测试矩阵先加入“批准 replenishment 后提交 update_price”“批准 100 后提交 101”“跨 tenant approval ID”等失败用例，确认旧协议无法给出完整保证。

## 根因分析

1. 审批工作流与 Mock 执行 UI 在不同阶段演进，双方只共享 Issue 状态，没有共享不可变 execution contract。
2. action hash 在审批恢复时校验，但没有成为执行接口唯一事实来源。
3. UI 为便于演示保留自由 action selector 和 quantity editor，导致客户端拥有业务参数。
4. 幂等围绕请求而不是围绕 approval 的唯一 side-effect 权利设计。

## 修复

- 新增 manual_execution_contract，由服务器在审批创建时绑定 tenant、active Amazon connection、marketplace、Issue、RecommendedAction、canonical plan、contract version 和 action hash。
- 执行接口只接受 approval_request_id、operator notes、execution evidence 和必需的 Idempotency-Key。
- 明确禁止客户端 action_type、quantity 和业务 changes。
- 执行前重新加载审批和资源，校验状态、过期、审批独立性、policy、connection、marketplace、evidence、plan 和 action hash。
- ApprovalRequest 与 ManualExecution 建立唯一关联；相同 key 安全重放，不同 key 不能重复执行同一 approval。
- 并发 reservation 只授予一个 side-effect 权利。
- provider 失败持久化 failed，必须人工恢复，不自动重试为成功。
- 前端只列出服务端验证过的 completed approval plans，展示精确动作、数量、connection、hash、expiry 和 reviewers。

## 验证结果

新增 manual execution contract、repository 和 API 测试，覆盖计划篡改、跨租户、pending、expired、awaiting second approval、相同/不同幂等 key、provider failure 和并发 reservation。修复提交形成时的全量结果为 632 passed、8 skipped；当前候选工作树的最新全量结果见面试资料索引。Decimal 在数据库往返时由 1 变为 1.000000 的 hash 漂移也被发现并通过 canonical normalization 修复。

## 残余风险

- 当前完整协议在 Mock adapter 上验证，真实 Amazon write adapter 仍未开放。
- PostgreSQL 下的并发唯一执行需要 CI/外部数据库继续验证。
- 浏览器级身份切换和 Compose E2E 尚未完成。
- policy version 被撤销后的长期审批处理需要正式运营 runbook。

## 经验

审批不是一个 status，而是一份不可变授权契约。任何 side effect 的业务参数都必须来自服务器端已批准快照，客户端只能提交执行凭证和非业务备注。Mock 环境不能拥有比未来生产更弱的完整性规则。
