# 失败复盘 01：真实模型 Schema Repair 与高延迟

## 摘要

受控 staging 记录显示，四个真实模型案例全部触发 Schema repair，端到端延迟约为 35.8 至 89.3 秒。系统最终能够返回结构化结果，但首次 Schema 成功率为 0%，repair rate 为 100%。这不满足交互式运营控制塔的体验，也使成本、超时和 provider failover 风险放大。

## 影响

- Operator 等待时间过长，面试演示容易超时。
- 每次 repair 增加 Token、费用和第二次 provider 失败概率。
- “最终 Schema 成功”掩盖了首次失败，若只报告最终成功率会形成失真指标。
- fallback 场景可能同时消耗主供应商失败预算和备用供应商修复预算。

## 发现方式

真实模型 smoke 将 initial response、repair_attempted、latency、Token、cost 和 failure category 分开记录。EXTERNAL_STAGING_VERIFICATION_RUNBOOK.md 保留了四个案例的原始时延范围。后续版本化评测报告明确把 first-pass schema success 和 repair rate 拆成独立指标，不再用最终 structured success 替代。

## 根因分析

1. 旧 Agent 契约含有较多开放 dict 和宽泛字段，模型需要推断形状。
2. Schema 和 prompt 体积偏大，重要约束被大量说明稀释。
3. 部分提示依赖“最多 N 条”等自然语言，而不是 JSON Schema 的 maxItems/maxLength。
4. 初始输出和 repair 输出使用相似上下文，repair 没有充分压缩到具体 validation error。
5. provider 对复杂 discriminated shape 的遵循能力未通过版本化批次验证。

## 修复

- 将 task draft、listing change、difference cluster、read-only check 和 action arguments 改为强类型模型。
- 使用 discriminated union，拒绝未知 action type 和参数注入。
- 为字符串、集合、evidence 数量和输出条数增加 Schema 上限。
- 新增 contract adapter，隔离模型输出版本与内部持久模型。
- 生成 schema-size 版本证据，防止后续无意膨胀。
- 将 schema failure category、initial latency、repair latency、fallback used 分别记录。
- 建立版本化 JSONL 数据集和 deterministic/fixed-fixture 报告。

## 验证结果

本地 fixed-fixture 12/12 首次 Schema 成功，repair rate 为 0%，但这只证明新契约、评测器和固定输出可以正确工作，不证明真实 provider 已优化。当前正式报告仍为 no-go，real provider quality 为 not_verified。

## 尚未完成

- 使用同一模型版本、温度、超时和 prompt version 重新跑至少 30 至 50 个真实案例。
- 报告 first-pass schema Wilson 区间、repair rate、P50/P95、Token 和 cost。
- 对每个失败分类保留脱敏 response digest，不提交敏感原文。
- 设定候选门禁，例如 first-pass schema success >= 95%、repair rate <= 5%、P95 满足演示 SLA。

## 经验

结构化输出不能只看“最终能否 parse”。初始成功率、修复率和修复成本必须成为一等指标。没有真实 provider 批次证据时，最诚实也最工程化的结论是 no-go，而不是用 synthetic 结果推断线上质量。
