# OmniSell 生产就绪审计与全员上线计划

审计日期：2026-07-16
审计范围：当前仓库、生产配置、后端与前端镜像、迁移、租户隔离、Amazon 连接与写入门禁、审批、任务队列、审计、备份恢复脚本和运行手册。

## 1. 执行结论

当前结论是 **有条件就绪（Conditional Go）**：

- 可以继续进行本地 E2E、Golden Mock 演练和内部预生产验收。
- 在完成本文 P0 外部门槛后，可以进入少量真实员工、单一卖家、只读 Amazon Pilot。
- 当前不能直接宣布“公司所有人生产可用”，因为企业身份、真实 Amazon 应用、托管基础设施、告警接收端、模型供应商、备份仓库和业务审批规则都属于仓库外部依赖，尚未用真实环境验证。
- Amazon 写操作应继续保持关闭。只有完成 P1 写操作门槛、真实对账和分阶段 canary 后，才能按动作逐项开放；退款、支付逆转、自主取消和跨租户操作仍然禁止。

## 2. 已完成的仓库内工作

### 2.1 产品与确定性边界

- 产品定位为 Amazon 运营控制塔，而不是自由聊天入口。
- 金额、费用、利润、库存、权限、策略、审批和状态迁移由确定性代码负责；模型仅生成建议或结构化草稿。
- 利润与价格计算使用 `Decimal`；持久化时间使用 UTC；建议输出保留证据、置信度、缺失数据和降级原因。
- Golden Mock、演练数据和真实连接数据在界面/API 中有明确模式与免责声明，避免把模拟数据表述为 Amazon 已验证数据。

### 2.2 身份、授权与租户隔离

- 生产模式只允许 OIDC，要求 HTTPS issuer/JWKS 和 MFA；API key/hybrid 不能用于生产。
- 前端生产模式要求 Auth.js secret、OIDC client 和后端 URL，并拒绝示例占位符。
- 业务 API 从认证 principal 获取 `tenant_id`，审批与审计持久化查询缺少 tenant 时 fail closed。
- PostgreSQL 模型和迁移包含 tenant 索引、合理唯一约束与 RLS；应用 session 自动设置 `app.current_tenant_id`。
- 真实 PostgreSQL 16 测试确认应用角色无法读取或写入其他 tenant 的审计记录。

### 2.3 Side effect 与 Amazon 安全

- Amazon 写入同时受全局开关、tenant 开关、只读 pilot、scope、策略、审批、action hash 和审计约束。
- 高风险批准绑定 action hash；参数变化会使批准失效。
- 通知回调 token 使用 `X-OmniSell-Notification-Token` 请求头与 constant-time 比较，不再放在 URL 中。
- 通知与异步任务具备去重、幂等和持久状态；可从 sync cursor、inbox 和 durable task 状态恢复。
- 退款、支付逆转、补偿发放、自主取消和跨租户操作继续代码级阻断。
- 旧 `/api/sync-product` 在生产返回 404；非生产真实执行仍要求确认和写/admin scope。

### 2.4 数据、迁移与恢复

- 生产要求 PostgreSQL，应用角色与迁移角色必须不同并使用非占位符密码。
- Alembic 当前单一 head 为 `0014`；空 PostgreSQL 16 数据库从 `0001` 到 `0014` 顺序升级成功。
- Redis 生产 URL 必须带非占位符密码；Celery 是生产任务后端。
- 本地、异地备份与隔离恢复脚本已提供；本地 dump 使用临时文件后原子改名，恢复默认进入 `omnisell_restore`，不会直接覆盖生产库。
- 恢复切换前要求检查行数、Alembic revision、租户隔离和应用 smoke。

### 2.5 配置、镜像与边界防护

- 后端和前端提供独立 production env 示例；真实 env 被 Git 忽略并从 Docker build context 排除。
- 生产设置拒绝 localhost/HTTP CORS、SQLite、同一数据库角色、无密码 Redis、无效 Fernet key、示例 token、自动建表、memory task backend、mock drill 和 LLM 内容遥测。
- API、worker、scheduler 和 migrate 使用同一不可变后端镜像；API 镜像以 `omnisell` 非 root 用户运行。
- 前端 standalone 镜像以 `node` 非 root 用户运行。
- 生产 Compose 不发布 API 8080；仅 Caddy 暴露 80/443。
- Caddy 配置包含 HSTS、nosniff、frame deny、Permissions Policy、UI CSP 和 API 请求体 16 MB 上限。
- 公共生产 health 仅返回服务状态，不泄露内部依赖和队列信息。

### 2.6 可观测性与降级

- 请求、策略、模型与 agent run 支持结构化日志、指标和 trace 上下文。
- LLM prompt/response 内容遥测在生产强制关闭，避免业务数据或 PII 进入观测后端。
- RAG 向量后端故障会产生不含原始查询的结构化 warning，并对 FAQ 请求回退到确定性本地检索；非 FAQ 类型 fail closed 返回空结果。
- 告警规则和 runbook 已定义 Amazon 授权失败、SP-API 429/5xx、同步延迟、队列积压、API 5xx、数据库/Redis 和模型错误等信号。

## 3. 2026-07-16 验证证据

| 验证项 | 结果 |
| --- | --- |
| Python compileall | 通过 |
| Alembic heads | `0014 (head)` |
| 全量 pytest | `189 passed, 2 skipped` |
| 高风险聚焦测试 | `68 passed, 2 skipped` |
| 生产配置/Agent 聚焦测试 | `71 passed` |
| PostgreSQL 16 空库迁移 | `0001 -> 0014` 通过 |
| PostgreSQL 16 RLS | `2 passed`，跨 tenant 读写被阻断 |
| Python runtime/dev 依赖审计 | 两份均 `No known vulnerabilities found` |
| 前端 TypeScript | `tsc --noEmit` 通过 |
| 前端 production dependency audit | `0 vulnerabilities` |
| API Docker build | 通过，运行用户 `omnisell` |
| Frontend Docker build | 通过，运行用户 `node` |
| API container smoke | `/api/admin/health` 正常 |
| Frontend container smoke | `/signin` 返回 200 |
| Frontend production env guard | 占位符被拒绝，格式合规测试配置返回 200 |
| Production Compose merge | 通过，仅 Caddy 发布 80/443 |
| Caddy validate | `Valid configuration` |
| Backup/restore shell syntax | 3 个脚本均通过 `bash -n` |
| Git diff whitespace check | 通过；仅存在 Windows LF/CRLF 提示 |

两个默认 skip 是需要显式 `POSTGRES_RLS_URL` 的真实数据库测试；本次另行启动 PostgreSQL 16 后已执行并全部通过。

前端构建仍显示 NextAuth/Jose 对 Edge Runtime 的 `CompressionStream`/`DecompressionStream` 兼容性警告，但构建成功且登录页容器 smoke 通过。升级 Auth.js/Next.js 时应持续监控该警告，不能把它当作当前上线失败。

## 4. 上线门槛

### P0：任何真实员工使用前必须完成

- [ ] 确定生产域名、DNS、TLS 入口、网络边界、WAF/负载均衡和允许的前端 origin。
- [ ] 接入公司 OIDC，验证 issuer、JWKS、audience、MFA claim、员工停用、session 过期和密钥轮换。
- [ ] 定义员工到 tenant、role、scope 的权威映射；禁止把用户可编辑字段当作 tenant 来源。
- [ ] 部署托管 PostgreSQL 16、认证 Redis、Secret Manager 和对象/备份存储；创建不同的 app/migration 角色。
- [ ] 用真实 Secret Manager 注入所有生产变量，确认仓库、镜像、日志、任务和 URL 中没有明文 secret。
- [ ] 选择并批准模型网关、模型版本、区域、数据处理条款、保留策略、预算和速率限制。
- [ ] 连接日志/指标/trace 后端，配置生产 dashboard、paging 通道、on-call 和告警演练。
- [ ] 执行一次真实异地备份和隔离恢复演练，记录 RPO、RTO、恢复证据和切换批准人。
- [ ] 完成隐私、PII、客户消息、Amazon 数据、审计日志和模型输入输出的保留/删除政策。
- [ ] 在预生产运行完整 OIDC、Amazon OAuth、只读同步、通知去重、对账和断点恢复 smoke。

### P1：开放任何 Amazon 写操作前必须完成

- [ ] 取得并验证 Amazon Draft/Public 应用、LWA 凭证、redirect URI、marketplace 范围、角色和配额。
- [ ] 为每个写动作定义 owner、风险等级、证据要求、最低置信度、审批人、双人复核、超时和回滚方法。
- [ ] 由财务/运营批准成本口径、币种、税费、FBA/仓储、退货损失和利润底线。
- [ ] 由供应链批准库存健康阈值、安全库存、补货周期和缺货/积压策略。
- [ ] tenant 写开关默认 false；先按单 tenant、单 marketplace、单动作、少量 SKU canary。
- [ ] 每次写后执行 Amazon read-back 和本地 reconciliation；不一致自动停止后续写入并创建人工任务。
- [ ] 完成 action hash、重复提交、超时重试、部分失败、撤销/补偿和审批失效演练。
- [ ] 退款、支付逆转、补偿发放、自主取消和跨租户写入不得纳入自动化范围。

### P2：扩展到公司所有人前必须完成

- [ ] 完成负载测试和容量模型：并发员工、tenant 数、SKU/订单量、通知峰值、队列积压和模型预算。
- [ ] 定义服务 SLO、支持时间、严重级别、升级路径、变更窗口和发布/回滚责任人。
- [ ] 建立员工培训、可接受使用政策、数据分类、审批责任和异常上报流程。
- [ ] 建立 joiner/mover/leaver 自动化，至少每季度复核 tenant membership、admin 和 approver 权限。
- [ ] 建立模型/提示词/策略/成本口径/迁移版本的变更审批与审计流程。
- [ ] 完成业务连续性演练：OIDC 不可用、Amazon 限流、数据库故障、Redis 故障、模型故障和通知丢失。

## 5. 推荐推广顺序

1. **预生产验收（2–3 天）**：平台负责人、安全、运营、财务使用合成数据和真实身份登录，完成 P0 smoke。
2. **只读 Pilot（至少 5 个工作日）**：1 个 tenant、1 个卖家、5–10 名核心运营，只同步和生成建议，不执行 Amazon 写入。
3. **部门只读推广（至少 1–2 周）**：增加运营、客服、供应链和财务；监控数据新鲜度、建议采纳率、误报、延迟和支持工单。
4. **受控写入 canary**：只开放一个可逆、低风险动作；单 tenant、少量 SKU、强制审批和 read-back reconciliation。
5. **逐动作扩展**：每个动作单独通过安全、财务、运营验收；不使用“总开关一次全开”。
6. **公司全员使用**：仅在 P2、容量、培训、权限复核和支持机制完成后开放；默认最小权限，非业务人员不获得 Amazon 写 scope。

建议每阶段设置明确退出条件：严重安全事件为 0、跨 tenant 数据暴露为 0、未批准 side effect 为 0、关键同步完整率达到业务目标、告警可到达 on-call、恢复演练满足批准的 RPO/RTO。

## 6. 上线当天检查表

- [ ] 镜像 digest 固定并完成漏洞扫描；禁止临时从开发目录启动。
- [ ] 生产 env 全部由 Secret Manager 注入，配置启动校验通过。
- [ ] Alembic migration job 成功，应用角色不能执行 DDL。
- [ ] OIDC 登录、MFA、tenant/role/scope、员工禁用和 session logout 验证通过。
- [ ] API 仅通过 TLS 反向代理访问；数据库、Redis 和内部 API 不直接暴露公网。
- [ ] Amazon 写入全局和 tenant 开关均为 false，直到批准 canary 窗口。
- [ ] 只读同步、通知去重、cursor 恢复、队列重试、dead-letter 和 reconciliation 验证通过。
- [ ] Dashboard、告警、on-call、状态沟通和回滚负责人在线。
- [ ] 上线前备份完成并验证可读；最近一次恢复演练在有效期内。
- [ ] 记录 Go/No-Go 决策、批准人、已知风险和下一次复盘时间。

## 7. 仍需外部验证的风险

- 尚未使用公司的真实 OIDC claims、人员目录和离职停权流程验证权限生命周期。
- 尚未使用真实 Amazon SP-API 应用、卖家账户、marketplace、通知目的地和生产配额验证端到端行为。
- 尚未在最终云环境验证网络、TLS、Secret Manager、PostgreSQL HA、Redis HA、对象存储和真实 restore。
- 尚未使用最终模型/网关验证质量、幻觉率、结构化输出成功率、延迟、成本和数据驻留。
- 尚未连接最终观测平台、paging 渠道和 on-call，因此告警“定义完成”不等于“实际可达”。
- 尚未根据真实并发、tenant、SKU、订单和通知量完成容量/压力测试。
- 尚未由法务、安全、财务、运营和供应链批准各自的数据政策与业务阈值。

这些项目不能通过继续修改本地代码替代，必须在用户提供真实环境信息后执行集成与验收。

## 8. 最后需要用户提供的资料与决策

为避免多轮零散索取，后续可以一次性提供下列信息；不要在聊天中直接发送长期 secret，优先提供 Secret Manager 路径、临时测试凭证或由部署平台注入：

1. **部署与网络**：云/机房平台、区域、公司域名、DNS 管理方式、TLS/WAF/LB、出入站限制、目标高可用级别。
2. **身份**：OIDC issuer、JWKS、audience、client 配置、MFA claim、group/role claim、员工与 tenant 映射规则、管理员和审批人名单。
3. **Amazon**：应用状态（Draft/Public）、LWA/application 配置、redirect URI、卖家与 marketplace、通知/bridge 方案、所需 SP-API roles、配额和首批 pilot tenant。
4. **数据基础设施**：PostgreSQL/Redis endpoints 与 Secret Manager references、备份对象存储或 restic repository、RPO/RTO、保留期限、数据驻留要求。
5. **模型**：供应商、base URL、模型名、认证注入方式、允许的数据类型、区域/保留条款、月度预算、延迟和质量目标。
6. **可观测性**：OTLP endpoint/headers 的注入方式、日志平台、告警渠道、on-call 排班、严重级别和 SLO。
7. **业务规则**：成本/利润口径、库存策略、允许自动化的动作、每个动作的审批阈值、双人复核规则、canary 范围和停止条件。
8. **治理**：数据分类、PII/客户消息政策、审计访问人、保留/删除政策、法务/安全/财务/运营批准人。
9. **规模**：预计员工并发、tenant 数、卖家数、marketplace、SKU/订单/通知日峰值和增长预期。
10. **发布运营**：上线窗口、负责人、支持渠道、培训对象、试点名单、Go/No-Go 会议和回滚授权人。

## 9. 相关操作文档

- `DEPLOYMENT.md`：生产构建、迁移和启动命令。
- `docs/RUNBOOK.md`：本地 E2E、生产部署、告警、恢复和安全操作。
- `docs/PRODUCTION_PILOT.md`：真实 Amazon 只读 pilot 步骤。
- `docs/MOCK_PRODUCTION_PILOT.md`：无真实凭证时的生产形态演练。
- `.env.production.example` 与 `frontend/.env.production.example`：生产变量模板，不能直接作为生产 secret 使用。
