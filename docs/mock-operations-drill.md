# Mock Amazon 运营演练

该模式只用于本地和预发布演练，所有平台对象都必须显示：

- `data_source=mock_amazon`
- `verified_amazon_data=false`
- `scenario_id=<固定场景>`

## 启动约束

```env
AMAZON_WRITES_ENABLED=false
AMAZON_PILOT_READ_ONLY=true
MOCK_DRILL_ENABLED=true
NEXT_PUBLIC_MOCK_DRILL_ENABLED=true
```

## 数据闭环

1. 运行迁移和 `local_e2e_mock/bootstrap_tenant.py`。
2. 运行 `scripts/seed_golden_mock_tenant.py`，仅创建 10 个库存策略和 9 个成本档案。
3. 完成 Mock Amazon 授权并执行一次订单、库存、Listing 全量同步。
4. 同步成功后自动创建 `operations.refresh` Durable Task。
5. 在 `/mock-scenarios` 重置并触发场景。
6. 在 `/issues` 由 Operator 认领、处理并提交审批。
7. Approver 在 `/approvals` 批准建议；发起人不能自批。
8. Operator 在 `/mock-seller-central` 手工执行。
9. Mock Notification 进入 Inbox，触发增量同步和运营刷新。
10. 确定性验证通过后异常进入 `resolved`，否则进入 `verification_failed`。

## 四个演练身份

- `owner@local.omnisell.test`
- `operator@local.omnisell.test`
- `approver@local.omnisell.test`
- `viewer@local.omnisell.test`

浏览器默认 OIDC Persona 仍为 Owner；其他角色可通过 Mock OIDC password/client-credentials Token 验证 API 权限。

## 安全边界

Mock Seller Central 只调用独立 `MockSellerCentralPort`，不会调用 Amazon SP-API 写接口。退款、赔付、付款、自动取消和真实 Amazon 修改仍被硬阻断。
