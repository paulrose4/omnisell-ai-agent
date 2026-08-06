# API 与面试演示范围

## 范围审计

当前版本化 route inventory 包含 136 个后端 method/path 条目；前端 69 个 BFF 文件导出 83 个 HTTP handler，其中 81 个代理映射到已注册后端路由，2 个属于 NextAuth。后端数量由实际 FastAPI `APIRoute` 注册结果生成，前端映射由 TypeScript AST 生成，不再依赖文本搜索；两份证据均有 `--check` 漂移门禁。

正式发布配置分为：

| Profile | 后端路由数 | 用途 | 外部写入边界 |
| --- | ---: | --- | --- |
| `full_development` | 136 | 本地开发、扩展模块和实验能力 | 仍受授权、Policy、审批和 adapter gate 约束 |
| `golden_demo` | 77 | 面试黄金闭环和 Mock Seller Central 演示 | 只允许明确 Mock/内部状态流，不注册扩展业务 API |
| `read_only_pilot` | 119 | 生产与真实 Amazon 只读 Pilot | 禁止外部 Amazon 写入；允许同步、审批、审计等内部状态写入 |

旧 `full`、`interview` 仅作为兼容别名保留到 `2.0.0`。生产环境强制 `read_only_pilot`，Mock 和 legacy 路由不得注册。Profile 是发布面控制，不替代 auth、tenant scope、RLS、Policy、action hash、审计或恢复语义。

该规模体现了平台化能力，但不适合在面试中逐项展示。候选版本采用“一个黄金闭环 + 扩展模块索引”的策略，避免让功能数量掩盖 Agent Runtime、安全和确定性边界。

## 默认面试导航

`golden_demo` 只展示以下入口，并由后端路由注册、前端导航和 middleware 三层共同限制：

| 导航 | 目的 |
| --- | --- |
| 总览 | 显示租户、Amazon connection、同步状态和关键风险 |
| 异常 | 展示缺货 Issue、确定性库存证据和 Advisor 分析 |
| 审批 | 展示 policy decision、risk level、action hash、审批人数和过期时间 |
| Mock Seller Central | 展示服务器端已批准计划和人工执行 |
| Agent 监控 | 展示运行状态、Schema repair、Token、成本、延迟和降级 |

广告、VOC、Listing、客服、对账、学习闭环等能力保留为扩展模块，不进入默认黄金叙事，也不删除对应代码和测试。

## 黄金闭环验收

演示必须按固定顺序运行：

1. 重置单一演示租户。
2. 触发 Mock 店铺缺货场景。
3. 执行同步并生成 stockout Issue。
4. Operator 认领 Work Item。
5. Advisor 生成只读解释，引用确定性 evidence ID。
6. 创建绑定 active connection 和 marketplace 的审批请求。
7. 独立审批；需要时完成第二审批人。
8. 在 Mock Seller Central 选择服务器列出的 approved plan。
9. 使用稳定 Idempotency-Key 执行一次。
10. 消费事件并重新同步。
11. 验证 Issue resolved、ManualExecution verified、Work Item completed。

API/ASGI 自动验收已经覆盖上述领域状态。浏览器级身份切换、Compose 基础设施和 Playwright 端到端仍是候选发布前的外部门禁，不得用 API 测试冒充浏览器验收。

## 必须展示的负路径

- tenant A 无法读取或执行 tenant B 的 approval_request_id。
- pending、rejected、expired、awaiting_second_approval 都不能执行。
- 客户端提交 action_type、quantity 或其他业务参数会被拒绝。
- approved plan 被修改时返回 approved_plan_mismatch，且 write port 调用次数为 0。
- 相同 Idempotency-Key 重试返回同一 execution；不同 key 不能重复执行同一 approval。
- provider 写入失败会持久化 failed，不会自动宣称成功。

## API 治理策略

- 为黄金闭环建立明确 owner：Operations Domain、Approval Safety、Manual Execution、Frontend Control Tower。
- 已生成后端 route inventory，记录 method、path、owner、auth scope、tenant scope、risk、stability、data provenance、write capability、environment、profile，以及逐路由副作用、幂等、Policy、审批、审计、恢复和外部写入状态。
- 新增 API 必须进入 catalog，并说明属于 `golden_demo`、`read_only_pilot` 或仅 `full_development`；未分类时 CI 失败。
- side effect route 没有 authorization、policy、idempotency、action hash、audit 和 recovery 任一项时不得合并。
- BFF 只转发经过后端授权的协议，不拥有业务事实，也不能重构 approved plan。
- 已为高风险 method 增加逐路由 override，并由默认规则保证读路由无副作用、写路由必须声明审计与恢复语义。
- 已生成前端 BFF inventory，校验每个 BFF 的 upstream target、method、OIDC auth/tenant 传播和允许 profile；NextAuth handler 显式标记。
- middleware 使用生成的 profile manifest，在 BFF 代理前 deny-by-default，而不是等待后端返回 404。
- 已版本化完整 `full_development` OpenAPI schema，并生成不可变前端 API 类型；catalog、OpenAPI 和 BFF manifest 三方漂移均由 CI 检查。profile 只负责暴露面，业务授权继续 fail closed。
- 下一步在 Compose/Playwright 中用真实 OIDC session 验证 profile 允许与拒绝路径，静态 manifest 不替代运行时证据。

## 面试时间分配

- 5 分钟：业务问题和为什么不是自由 Agent。
- 8 分钟：系统架构、确定性边界和 Agent Runtime。
- 12 分钟：缺货黄金闭环。
- 8 分钟：审批篡改、重放、跨租户和 provider 失败。
- 5 分钟：评测报告、真实模型失败和后续 Pilot。

其余时间用于代码深挖，优先展示 `domain/inventory.py`、`domain/profitability.py`、`agents/runtime.py`、`agents/validators/evidence.py`、`safety/policy_engine.py`、`workflows/engine.py`、`application/manual_execution_contract.py` 和 `api/route_catalog.py`。
