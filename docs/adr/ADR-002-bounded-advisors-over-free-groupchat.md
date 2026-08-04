# ADR-002：使用有界 Advisor，而不是自由 GroupChat

Status: Accepted for interview candidate

Date: 2026-08-05

## Context

OmniSell 的目标是 Amazon 运营控制塔。它需要解释库存、利润、价格、客服和对账证据，但金额、权限、审批和状态迁移必须可预测、可审计。早期 AutoGen GroupChat demo 允许多个 Agent 自由对话和选择工具，这种结构适合探索，但难以回答谁拥有状态、哪一次输出是授权依据、如何预算 Token、如何恢复以及如何证明没有越权。

生产候选版本还需要严格 tenant scope、evidence citation、freshness、Schema、超时、成本、一次 repair、降级和持久化工作流。自由 GroupChat 的开放调度会扩大状态空间和测试组合，面试官也容易误判它是生产入口。

## Decision

生产入口采用 bounded advisor architecture：

- 每个 Advisor 有明确 agent_type、prompt_version、输入证据类型和输出 Schema。
- PersistentAgentWorkflow 拥有状态机、恢复、审批和审计。
- AgentRuntime 拥有 evidence/context/token/cost budget、timeout、repair 和 fallback。
- EvidenceValidator 在结果进入业务流程前验证引用、资源、freshness、置信度、禁止动作和确定性值。
- Advisor 只生成解释、假设、缺失数据和内部草稿，不获得 side-effect 执行权。
- AutoGen GroupChat 移入 legacy/autogen_demo，只保留教学和历史参考价值。

## Alternatives

1. 保留自由 GroupChat 作为生产 orchestrator。拒绝原因：工具选择、状态、预算、恢复和审批路径难以穷举验证。
2. 使用单一超大 Prompt 覆盖所有业务域。拒绝原因：Schema 膨胀、上下文噪声、失败分类和领域 ownership 不清晰。
3. 完全不用 LLM。拒绝原因：跨证据解释、假设表达、缺失数据说明和内部草稿的人工成本较高，纯规则无法提供同等可读性。
4. 让 workflow framework 自动决定所有状态。拒绝原因：关键状态、审批和 side effect 仍需要领域显式建模，而不是隐藏在框架回调中。

## Consequences

正面结果：

- 每个 Agent 的输入、输出、预算和失败模式可独立测试。
- 状态和 side effect 由确定性工作流拥有，恢复语义清晰。
- 可以分开报告 first-pass schema、repair、citation、latency、Token 和 cost。
- Legacy demo 与生产架构不会混淆。

代价：

- 需要为每个 Advisor 维护 Schema、adapter、prompt version 和数据集。
- 跨 Advisor 协作必须通过显式 workflow 和 typed contract，开发速度低于自由对话 demo。
- 新业务域不能只增加一个角色说明，必须补齐 evidence、validator、eval 和 ownership。

后续约束：

- 不得从 legacy/autogen_demo 导入生产 API 路由。
- 新 Advisor 必须先定义 deterministic boundary、typed contract、versioned eval 和降级行为。
- 若未来引入 multi-agent coordination，也必须由持久化状态机和预算控制，不恢复自由工具自治。
