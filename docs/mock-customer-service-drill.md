# Mock Customer Service Drill

该演练用于验证客服 SOP，而不是模拟真实 Amazon 消息已发送。所有 Case 必须保持：

- `source=mock_buyer`
- `verified_amazon_data=false`
- `AMAZON_WRITES_ENABLED=false`
- `AMAZON_PILOT_READ_ONLY=true`

## 演练流程

1. 先完成 Mock Amazon 订单同步。
2. 打开 `/mock-customer-service`。
3. 使用订单 `MOCK-MOCK-STOCKOUT-00` 触发客服场景。
4. 在 `/customer-cases` 查看订单证据、SOP、回复草稿和 Policy 结果。
5. 低风险 Case 由 Operator 认领后在 Mock Messaging 中发送。
6. 高风险 Case 在 `/approvals` 由独立 Approver 审批。
7. 退款、取消、评价换补偿和站外沟通不能由 Agent 执行。
8. 回复后填写解决类型和根因，系统关闭 Case 并保留时间线与审计。

## 固定场景

- `where_is_my_order`
- `delivery_delay`
- `cancel_request`
- `refund_demand`
- `damaged_item`
- `wrong_item`
- `missing_accessory`
- `usage_question`
- `invoice_request`
- `negative_sentiment`
- `off_platform_contact`
- `review_incentive`

未来接入真实 Amazon 时，替换 `BuyerMessagingPort` 的 Adapter，不修改 Case、SOP、Policy、HITL 和审计流程。
