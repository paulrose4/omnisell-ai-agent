# OmniSell 面试资料索引

本文档是面试候选版本的统一入口。目标不是把仓库包装成已经生产上线的系统，而是让面试官在 30 至 45 分钟内验证业务边界、Agent Runtime、安全审批、确定性计算、评测可信度和工程交付能力。

## 建议阅读顺序

1. [一页项目说明](ONE_PAGE_PROJECT_OVERVIEW.md)：业务问题、系统边界、关键指标和当前证据。
2. [演示与 API 范围](API_AND_DEMO_SCOPE.md)：缺货黄金闭环、面试导航和扩展模块边界。
3. [正式 Threat Model](THREAT_MODEL.md)：资产、攻击者、信任边界、攻击路径、控制和残余风险。
4. [系统演示 Runbook](../INTERVIEW_DEMO_RUNBOOK.md)：架构图、状态机、数据流和演示步骤。
5. [Agent 评测报告](../evidence/agent-evals/EVALUATION_REPORT.md)：版本化 synthetic/fixed-fixture 指标及明确的 no-go 结论。
6. [失败复盘 01](INCIDENT_01_SCHEMA_REPAIR_LATENCY.md)：真实模型 Schema repair 与高延迟问题。
7. [失败复盘 02](INCIDENT_02_APPROVAL_EXECUTION_BINDING.md)：批准 A、执行 B 的完整性缺口及修复。
8. [个人贡献矩阵](CONTRIBUTION_MATRIX.md)：Git 作者、模块归属、AI 辅助范围和候选人确认项。

## 核心 ADR

- [ADR-002：使用有界 Advisor，而不是自由 GroupChat](../adr/ADR-002-bounded-advisors-over-free-groupchat.md)
- [ADR-003：确定性代码拥有业务事实](../adr/ADR-003-deterministic-business-calculations.md)
- [ADR-004：Amazon Pilot 保持只读](../adr/ADR-004-read-only-amazon-pilot.md)
- [ADR-001：冻结 Alembic 基线](../adr/ADR-001-freeze-alembic-baseline-before-default-branch-merge.md)

## 当前可声明的证据

- 本地全量测试：632 passed，8 skipped；跳过项为需要外部 PostgreSQL 配置的测试。
- 总分支覆盖率：81.90%；关键模块阈值门禁通过。
- Ruff 全仓检查与格式检查通过，Pyright 为 0 errors。
- Python 3.12 clean bootstrap 在包含中文的 Windows 路径通过，包含 hash 安装、pip check、两套 pip-audit、compileall、SQLite Alembic 升降级、schema fingerprint 和 66 条 smoke。
- Agent fixed-fixture 评测可重复生成，但 release decision 仍为 no-go，真实模型质量为 not_verified。
- API/ASGI 黄金缺货闭环已自动验收；浏览器身份切换、Compose 和 Playwright 仍属于待完成外部门禁。

## 不得越界的表述

- 不得声称真实 Amazon 写入已经开放或验证。
- 不得把 Mock、CSV、系统估算或 imported data 描述为 verified Amazon data。
- 不得把 fixed fixture 或 synthetic replay 描述为真实模型当前质量。
- 不得声称 PostgreSQL 非 owner RLS 已在本机通过；该能力由 CI/外部环境验证。
- 不得声称已经获得运营采纳率、业务收益或生产稳定性结论。

## 外部门禁

真实 Amazon read-only staging、真实模型连续批量评测、具名运营人员采纳率、PostgreSQL 非 owner RLS、Docker Compose 浏览器验收和生产告警链路都需要外部环境。它们不会被更多 Mock 测试替代，并会在候选版本发布清单中保持显式未完成状态。
