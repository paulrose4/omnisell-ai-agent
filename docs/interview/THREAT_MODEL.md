# OmniSell Threat Model

## 范围

本模型覆盖 OmniSell 只读 Amazon Pilot、Mock Seller Central 人工执行、Agent Runtime、审批恢复、前端 BFF、数据库和外部模型供应商。真实 Amazon 自动写入、退款、支付冲正、自主取消和跨租户操作不在允许范围内，并且必须 fail closed。

## 资产

- Amazon connection、OAuth token、OIDC identity、MFA 和权限 scope。
- tenant scoped 的订单、库存、利润、价格、Issue、Work Item、审批与执行记录。
- 金额、币种、库存数量、状态迁移和 policy decision 等业务事实。
- evidence bundle、content digest、freshness、action hash 和不可变 approved plan。
- Agent prompt、模型响应、Token/成本、评测数据集和审计日志。
- 数据库迁移、RLS policy、依赖锁、容器镜像和 CI 证据。

## 攻击者

- 未认证的外部用户。
- 已认证但权限不足的 tenant 用户。
- tenant A 中试图读取 tenant B 数据的恶意或被攻陷账号。
- 恶意 Operator、Requester 或 Approver。
- 通过商品文本、客户消息、知识库或模型上下文注入指令的内容提供者。
- 被攻陷或异常的模型、Amazon、OIDC、Redis、数据库或依赖供应商。
- 能修改客户端请求、重放请求、并发触发执行或篡改数据库快照的攻击者。

## 信任边界

1. Browser 与 Next.js BFF：浏览器输入不可信，BFF 不能拥有业务事实。
2. BFF 与 FastAPI：必须重新认证、授权、tenant scope 和验证协议。
3. API 与 PostgreSQL：应用过滤不是唯一边界，RLS 和非 owner role 是第二道边界。
4. Deterministic Domain 与 Agent Runtime：金额、库存、权限、policy 和状态不能从模型响应反向覆盖。
5. Agent Runtime 与 Model Provider：prompt、tool context 和模型输出均视为不可信输入。
6. Approval 与 Execution：审批时快照和执行时当前状态之间存在 TOCTOU 风险。
7. Application 与 Amazon/Mock Adapter：所有 side effect 都必须通过端口、幂等和审计。
8. Source Repository 与 CI/Dependency Registry：依赖、Action 和镜像是供应链边界。

## 数据流

Amazon 或 Mock 数据进入 tenant scoped repository，经确定性算法形成 EvidenceReference。Advisor 只读取有预算的 evidence bundle，输出严格 Schema。EvidenceValidator 校验证据 ID、资源、freshness、置信度、禁止动作和确定性值。PolicyEngine 产生 ALLOW、REQUIRE_APPROVAL 或 BLOCK；REQUIRE_APPROVAL 进入持久化 HITL。审批完成后，执行服务从 approved immutable plan 重建命令，验证 action hash、tenant、connection、marketplace、policy version、审批独立性、过期时间和幂等，再调用 adapter。事件重新同步后由确定性规则验证结果。

## 威胁矩阵

| ID | 威胁与攻击路径 | 影响 | 主要控制 | 验证证据 | 残余风险 |
| --- | --- | --- | --- | --- | --- |
| T01 | Prompt injection：商品、客户消息或 RAG 文本要求忽略规则并调用写工具 | 越权、泄露、错误建议 | Advisor 无执行权；tool ACL；严格 Schema；evidence ID 校验；永久禁止动作 | Agent contract、安全 eval、tool permission 测试 | 新型间接注入仍需真实 provider 红队 |
| T02 | 模型伪造 evidence ID、引用过期证据或修改金额/库存 | 幻觉、错误决策 | EvidenceValidator、freshness、deterministic_values、置信度上限 | evidence validator 100% branch coverage | metadata 来源本身被污染时需上游签名 |
| T03 | 跨租户 API 或 repository 查询 | 数据泄露 | tenant scoped query、identity claims、PostgreSQL RLS、tenant indexes | SQLite isolation 测试；PostgreSQL 测试脚本 | 本机未完成非 owner PostgreSQL 验证 |
| T04 | 审批语义混淆：REQUIRE_APPROVAL 被 medium 自动通过 | 未经人工批准执行 | policy decision 与 risk level 分离；只允许 ALLOW+LOW 白名单自动放行 | Policy/HITL 97.55%+ coverage；回归测试 | 历史数据兼容策略需生产迁移审查 |
| T05 | 审批篡改或批准 A、执行 B | 参数替换、TOCTOU | immutable plan、action hash、contract version、服务器端投影、执行前重验证 | manual execution contract/repository/API tests | 真实 Amazon adapter 尚未开放 |
| T06 | Requester 自批、同一人完成 critical 双批 | 职责分离失效 | requester/approver 独立；critical 两名不同身份；唯一约束/事务 | HITL 与 workflow integrity tests | IdP 账号共用需运营控制 |
| T07 | 重放、双击、并发请求重复 side effect | 重复补货或价格写入 | Idempotency-Key、approval 唯一 execution、reservation、事务 | repository concurrency 与 replay tests | 分布式数据库故障下需 PostgreSQL 验证 |
| T08 | 审批过期、证据漂移或 policy 撤销后仍执行 | 使用旧授权 | expires_at、evidence/action hash、policy re-evaluation、fail closed | workflow resume integrity tests | 长流程中的外部资源漂移需持续同步 |
| T09 | 模型供应商 504、fallback 降低预算预检 | 成本失控、延迟、不可用 | typed GatewayBudget、主备最坏预算、Token/费用聚合、超时和降级 | model gateway contract 100% branch coverage | 真实 provider P95 尚未重新验证 |
| T10 | PII 或 secret 被写入 prompt、日志、错误或 artifact | 隐私与凭证泄露 | redaction、secret store、日志字段白名单、artifact ignore、OIDC/OAuth state | audit redaction、secret scan、config tests | 真实数据分类和 DLP 仍需组织级策略 |
| T11 | 依赖或容器供应链漏洞 | RCE、凭证泄露 | hash lock、pip-audit、npm audit、SBOM、Trivy、pinned base digest | clean bootstrap、本地审计、CI jobs | registry compromise 和未披露漏洞 |
| T12 | 恶意运营人员在 Mock/真实控制台执行不同动作 | 审批绕过 | UI 无自由 action/quantity；API 只接收 approval_request_id；审计 | approved plan frontend/API tests | 真实 Seller Central 仍是人工外部系统 |
| T13 | 数据库 owner 绕过 RLS 或错误连接角色 | 跨租户读取 | 独立 app role、FORCE RLS、启动校验、CI PostgreSQL tests | migration/rls CI 设计 | 本地 Docker 未验证，运维配置仍可误设 |
| T14 | 迁移依赖实时 ORM 导致历史漂移 | 无法复现、数据损坏 | frozen 0023 baseline、schema fingerprint、Alembic check、ADR | SQLite fingerprint 与 migration tests | 已部署旧版本升级路径需真实环境决策 |

## 残余风险

- 真实模型的首次 Schema 成功率、repair rate 和 P95 延迟仍为 not_verified。
- 真实 Amazon read-only staging 尚未形成连续证据，真实写入明确关闭。
- PostgreSQL 非 owner FORCE RLS、并发执行和旧数据库升级需要 CI 或外部数据库。
- Prompt injection 数据集仍需扩展到多语言商品文本、客户图片 OCR 和恶意 RAG 文档。
- 具名运营人员的采纳率、误报率和审批疲劳尚无真实数据。
- 审计日志防篡改目前依赖数据库和访问控制，尚未接入外部不可变日志存储。

## 验证计划

1. CI 使用 PostgreSQL app role 执行空库升级、RLS catalog、跨租户负测试和并发审批。
2. Compose 启动 API、worker、Redis、PostgreSQL、OIDC Mock、Amazon Mock 和 frontend，运行 Playwright 身份切换黄金闭环。
3. 对版本化真实模型批次报告 citation、unsupported claim、refusal、first-pass schema、repair、P50/P95、Token 和 cost。
4. 对 prompt injection、PII、approval tamper、replay、duplicate execution 和 provider failure 进行发布前红队。
5. 只读 Pilot 期间每周审查告警、审计、误报、采纳、编辑和数据新鲜度。
6. 任何真实写入候选都必须新增 adapter-specific threat review，不得继承 Mock 的验证结论。
