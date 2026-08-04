# OmniSell 个人贡献与证据矩阵

## 使用规则

Git 只能证明提交作者字段和文件变化，不能自动证明现实身份、独立完成比例或设计决策归属。下面所有“候选人贡献”均以 Git 作者 paulrose4 为证据入口，但 paulrose4 是否就是候选人、哪些工作由候选人主导，候选人需确认并在最终面试版本签字。

## 已提交历史

| Git 证据 | 作者字段 | 主题 | 可审查范围 | 归属结论 |
| --- | --- | --- | --- | --- |
| 38c84fc | paulrose4 | repo hygiene | ignore、review、artifact hygiene | 候选人需确认 |
| 3ea61a3 | paulrose4 | platform foundation | 多租户、认证、数据库、生产基础 | 候选人需确认 |
| 2e08696 | paulrose4 | deterministic domains | inventory、profitability、pricing、operations | 候选人需确认 |
| c5b0ee7 | paulrose4 | governance | policy、HITL、workflow、approval | 候选人需确认 |
| 82fde0e | paulrose4 | agents | bounded advisor、runtime、recovery | 候选人需确认 |
| df36cee | paulrose4 | console | operations control tower frontend | 候选人需确认 |
| 7a0b86d | paulrose4 | UAT | high-fidelity Mock、acceptance eval | 候选人需确认 |
| f3de8d3 | paulrose4 | release | CI、部署、operator runbook | 候选人需确认 |
| 00de543 | paulrose4 | merge | 上游安全基线整合 | 候选人需说明 merge 内容 |
| b9cb749 | Ava | upstream safety | safety gate、permission model | 上游贡献，不应由候选人认领 |

## 当前优化工作树

当前未提交优化包含审批语义统一、approved plan 执行绑定、冻结迁移基线、依赖锁、Failover 最坏预算、强类型 Agent 契约、版本化评测、Legacy 隔离、工具链、覆盖率和面试资料。它们在提交、CI 和 clean clone 前只能称为本地实现，不能当作远端交付完成。

当前优化过程存在 AI 辅助，包括代码搜索、风险审计、测试用例建议、机械格式化、文档初稿和部分实现。AI 辅助范围必须诚实披露；历史提交是否使用其他 AI 工具不能从 Git 自动证明。

## 建议候选人签字矩阵

| 模块 | 建议声明 | 候选人必须能回答 |
| --- | --- | --- |
| 确定性业务域 | 主导或核心实现，待确认 | Decimal、币种、UTC、guardrail、测试边界 |
| Agent Runtime | 主导或核心实现，待确认 | evidence/context/cost budget、repair、fallback、degraded |
| 审批安全 | 主导或核心实现，待确认 | policy decision 与 risk、双人审批、hash、TOCTOU |
| 多租户与 RLS | 参与或主导，待确认 | tenant scope、DB session context、非 owner 验证 |
| 前端控制塔 | 参与或主导，待确认 | BFF 信任边界、身份、approved plan 展示 |
| 评测体系 | 当前优化工作，待提交 | dataset version、scorer、Wilson interval、no-go |
| CI 与交付 | 当前优化工作，待提交 | lock、audit、coverage、migration、external gap |

## AI 辅助与个人责任

面试时建议使用以下表述：

“我使用 AI 辅助进行仓库检索、测试矩阵扩展、机械修改和文档草拟，但架构边界、风险接受、验收标准和最终验证由我负责。对于金额、权限、审批和状态迁移，我没有把模型输出当作事实来源。所有 AI 生成变更都通过回归测试、静态检查和人工审查后才进入候选版本。”

候选人还必须能现场解释至少三个没有依赖 AI 自动生成答案的设计决策，并能定位一个真实失败案例的日志、代码、测试和修复提交。

## 发布前确认

- 确认 paulrose4 与候选人的现实身份关系。
- 为每个核心模块指定 owner 和 reviewer。
- 把当前工作树拆成逻辑提交，不混入无法解释的机械变化。
- 在 PR 中单独列出 Ava 的上游贡献。
- 记录使用 AI 的范围、候选人亲自运行的验证和仍未完成的外部门禁。
- 不使用“全部由我独立完成”之类无法从证据支持的表述。
