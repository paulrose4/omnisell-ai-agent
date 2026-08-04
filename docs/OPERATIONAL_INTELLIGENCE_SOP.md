# OmniSell 运营智能工作台 SOP

## 每日班次流程

1. 打开 `运营任务`，先处理紧急、逾期和验证失败任务。
2. 在 `广告与 Amazon 异常` 查看 Amazon 核心异常、广告诊断和 VOC 汇总；确认资源 ID、观测时间、采集时间、新鲜度和来源。
3. 认领任务或由 Team Lead 分配负责人，检查 SLA 截止时间和缺失数据。
4. 打开证据和只读检查建议；退款、支付撤销、自动取消、跨租户操作和 Amazon 自动写入始终禁止。
5. 对需要副作用的建议提交 HITL 审批；批准后由运营在 Seller Central 或广告控制台人工执行，并填写凭证。
6. 重新同步 Amazon 数据，等待确定性验证；只有验证通过的任务才能关闭。
7. 交班前生成交接包，包含未完成任务、最新证据、阻断原因、下一步和截止时间。

## 广告诊断

系统由代码计算 ACoS、TACOS、CTR、CVR、CPC、预算消耗和无转化花费。分母为零或缺失时结果是缺失，不会被转换成零。默认诊断包括：ACoS 高于盈亏平衡线、点击达到阈值但无订单、预算预计提前耗尽。Search Term 事实应来自只读 Advertising API 或明确标记的导入数据。

广告建议只生成内部 Work Item。出价、预算、否定词和广告结构调整仍需要负责人、SLA、审批和人工执行凭证。

## 退货与 VOC

退货、评论和客服标签按 Marketplace + Seller SKU + 时间窗口聚合。LLM 可以建议标签，但数量、比例、Top 原因和趋势由确定性代码计算。默认最小样本量为 5；未达到样本量显示 `insufficient_sample`，不生成高置信度产品改进任务。

可执行的产品改进任务必须写明原因码、证据、负责人和结果指标。完成后应在后续周期重新汇总退货率、低评分评论和客服问题，不能只记录“已处理”。

## Amazon 核心异常

Account Health、Buy Box、Listing Suppression、Stranded Inventory、Inbound Receiving、Shipment Quantity、Aged Inventory 和 Restock Limit 统一保存 `resource_id`、`observed_at`、`collected_at`、`freshness_seconds`、`source` 和 `verified_amazon_data`。只有 Amazon API 来源允许标记为已验证；Mock、手工和导入数据必须保留未验证标记。

这些信号只产生只读复核任务。任何修复动作仍需独立审批和人工记录。

## Listing 实验

实验前后快照必须同时记录 Sessions、订单、广告花费/流量占比/CPC、价格/优惠、Buy Box、可售库存/缺货小时、自然排名、促销、季节和竞争对手变化。

- `directional_improvement`：控制变量稳定、样本达到门槛，最多表示方向性改善，不表示因果证明。
- `confounded`：广告、价格、库存、Buy Box、促销、季节或竞品变量发生超过阈值的变化。
- `inconclusive`：控制变量、业务指标或样本不足。
- `rollback_recommended`：转化率或退货率触发保护线。

没有对照组或统计模型时，禁止在汇报中写“Listing 修改导致转化提升”。

## 批量和权限边界

批量动作仅允许认领、分配、设置截止时间、开始、阻断、解除阻断和交接。系统不提供批量审批、批量发布、批量退款、批量取消或批量 Amazon 写入。

## 外部上线前置条件

当前代码和 Mock/导入测试不能代替真实供应商验收。上线前必须使用真实但脱敏的 Advertising API、SP-API 只读账号和目标模型 Staging 配置，验证分页、限流、Token 撤销、部分失败恢复、观测时间映射、供应商 JSON Schema 和成本统计。未完成这些外部验收时，生产环境必须保持 `AMAZON_WRITES_ENABLED=false`、`AMAZON_PILOT_READ_ONLY=true`，并继续展示明确的数据来源和降级状态。
