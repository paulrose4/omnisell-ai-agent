# API 与面试演示范围

## 范围审计

当前仓库约有 123 个后端 HTTP/ASGI route decorator 和 69 个前端 BFF route 文件。该规模体现了平台化能力，但不适合在面试中逐项展示。候选版本采用“一个黄金闭环 + 扩展模块索引”的策略，避免让功能数量掩盖 Agent Runtime、安全和确定性边界。

## 默认面试导航

面试发布配置只展示以下入口：

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
- 生成 route inventory，记录 route、权限、tenant scope、side effect、idempotency、审计和演示级别。
- 新增 API 必须说明是否属于 golden、extension 或 internal。
- side effect route 没有 authorization、policy、idempotency、action hash、audit 和 recovery 任一项时不得合并。
- BFF 只转发经过后端授权的协议，不拥有业务事实，也不能重构 approved plan。

## 面试时间分配

- 5 分钟：业务问题和为什么不是自由 Agent。
- 8 分钟：系统架构、确定性边界和 Agent Runtime。
- 12 分钟：缺货黄金闭环。
- 8 分钟：审批篡改、重放、跨租户和 provider 失败。
- 5 分钟：评测报告、真实模型失败和后续 Pilot。

其余时间用于代码深挖，优先展示 domain/inventory.py、domain/profitability.py、agents/runtime.py、agents/validators/evidence.py、safety/policy_engine.py、workflows/engine.py 和 application/manual_execution_contract.py。
