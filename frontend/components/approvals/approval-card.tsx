"use client";

import { formatDistanceToNow } from "date-fns";
import { zhCN } from "date-fns/locale";
import { ChevronDown, ChevronUp, Clock3, FileJson, Fingerprint, ShieldAlert, ShieldCheck, UserRound } from "lucide-react";
import { useState } from "react";
import { ApprovalDecisionPanel } from "./approval-decision-panel";
import { StatusBadge } from "@/components/ui/status-badge";
import type { ApprovalRequest } from "@/lib/api/types";

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function firstValue(...values: unknown[]): unknown {
  return values.find((item) => item !== undefined && item !== null && item !== "");
}

function text(...values: unknown[]): string | null {
  const value = firstValue(...values);
  return value === undefined || value === null ? null : String(value);
}

function actionLabel(action: string): string {
  return ({ request_manual_execution: "运营动作人工执行审批", manual_execute_recommended_action: "库存运营动作审批" } as Record<string, string>)[action] ?? action.replaceAll("_", " ");
}

function Fact({ label, value }: { label: string; value: unknown }) {
  return <div className="border-b border-slate-100 py-2 last:border-0"><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 text-sm font-semibold text-slate-900">{value === undefined || value === null || value === "" ? "未提供" : String(value)}</dd></div>;
}

export function ApprovalCard({ approval }: { approval: ApprovalRequest }) {
  const [expanded, setExpanded] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const payload = approval.payload ?? {};
  const context = asRecord(payload.decision_context);
  const inventory = asRecord(context.inventory);
  const recommendation = asRecord(context.recommendation);
  const economics = asRecord(context.economics);
  const provenance = asRecord(context.provenance);
  const proposed = asRecord(payload.proposed_changes);
  const argumentsValue = asRecord(asRecord(payload.plan).arguments);
  const before = asRecord(payload.before_snapshot);
  const sellerSku = text(payload.seller_sku, proposed.seller_sku, argumentsValue.seller_sku, before.seller_sku);
  const marketplaceId = text(payload.marketplace_id, proposed.marketplace_id, argumentsValue.marketplace_id, before.marketplace_id);
  const reorderQuantity = firstValue(recommendation.recommended_reorder_quantity, payload.recommended_reorder_quantity, proposed.recommended_reorder_quantity, argumentsValue.recommended_reorder_quantity);
  const currency = text(economics.currency, payload.currency);
  const missingData = Array.isArray(context.missing_data) ? context.missing_data.map(String) : [];
  const alternatives = Array.isArray(recommendation.alternatives) ? recommendation.alternatives.map(String) : [];
  const secondStage = approval.status === "awaiting_second_approval";
  const created = new Date(approval.created_at);
  const verified = provenance.verified_amazon_data === true;

  return <article className="panel overflow-hidden" data-seller-sku={sellerSku ?? undefined}>
    <div className="p-5 lg:p-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0"><div className="mb-3 flex flex-wrap items-center gap-2"><StatusBadge value={approval.risk_level} />{approval.requires_second_verification && <span className="inline-flex items-center gap-1 rounded-full bg-rose-50 px-2.5 py-1 text-xs font-semibold text-rose-700"><ShieldAlert className="size-3.5" />{secondStage ? "等待第二人审批" : "双人审批"}</span>}<span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${verified ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>{verified ? "Amazon 已验证" : "来源未验证"}</span></div><h2 className="break-words text-lg font-semibold text-slate-950">{actionLabel(approval.action)}</h2><p className="mt-2 text-sm leading-6 text-slate-600">{text(recommendation.reason, approval.reason) ?? "等待独立审批决定。"}</p><div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold">{sellerSku && <span className="rounded-full bg-cyan-50 px-3 py-1 text-cyan-800">SKU {sellerSku}</span>}{reorderQuantity !== undefined && <span className="rounded-full bg-amber-50 px-3 py-1 text-amber-800">建议补货 {String(reorderQuantity)}</span>}{marketplaceId && <span className="rounded-full bg-slate-100 px-3 py-1 text-slate-700">{marketplaceId}</span>}<span className="rounded-full bg-emerald-50 px-3 py-1 text-emerald-700">仅人工执行</span></div></div>
        <button onClick={() => setDeciding((value) => !value)} className="shrink-0 rounded-lg bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white">{deciding ? "收起决策" : secondStage ? "进行第二次审批" : "开始审批"}</button>
      </div>

      <div className="mt-5 grid border-y border-slate-200 sm:grid-cols-3 sm:divide-x sm:divide-slate-200"><div className="py-3 sm:pr-4"><div className="flex items-center gap-2 text-xs text-slate-500"><UserRound className="size-4" />发起人</div><p className="mt-1 truncate text-sm font-medium">{approval.requester}</p></div><div className="border-t border-slate-200 py-3 sm:border-t-0 sm:px-4"><div className="flex items-center gap-2 text-xs text-slate-500"><Clock3 className="size-4" />创建时间</div><p className="mt-1 text-sm font-medium">{created.toLocaleString("zh-CN")}</p><p className="mt-1 text-xs text-slate-500">{formatDistanceToNow(created, { addSuffix: true, locale: zhCN })}</p></div><div className="border-t border-slate-200 py-3 sm:border-t-0 sm:pl-4"><div className="flex items-center gap-2 text-xs text-slate-500"><ShieldCheck className="size-4" />审批阶段</div><p className="mt-1 text-sm font-medium">{secondStage ? `第一审批人 ${approval.first_approver_id ?? "已记录"}` : "第一阶段"}</p>{approval.first_approved_at && <p className="mt-1 text-xs text-slate-500">{new Date(approval.first_approved_at).toLocaleString("zh-CN")}</p>}</div></div>

      <div className="mt-5 grid gap-x-8 lg:grid-cols-3">
        <div><h3 className="text-xs font-semibold uppercase text-slate-500">库存事实</h3><dl className="mt-2"><Fact label="可售 / 预留 / 在途" value={[inventory.sellable, inventory.reserved, inventory.inbound].map((item) => item ?? "-").join(" / ")} /><Fact label="覆盖天数 / 总提前期" value={[inventory.days_of_supply, inventory.total_lead_time_days].map((item) => item ?? "-").join(" / ")} /><Fact label="加权日销量" value={inventory.weighted_daily_sales} /><Fact label="预测置信度" value={inventory.forecast_confidence} /></dl></div>
        <div><h3 className="text-xs font-semibold uppercase text-slate-500">建议与影响</h3><dl className="mt-2"><Fact label="建议数量" value={reorderQuantity} /><Fact label="运输方式" value={recommendation.recommended_shipping_mode} /><Fact label="利润影响" value={economics.estimated_profit_impact ? `${economics.estimated_profit_impact} ${currency ?? ""}` : null} /><Fact label="成本影响" value={economics.estimated_cost_impact ? `${economics.estimated_cost_impact} ${currency ?? ""}` : null} /></dl></div>
        <div><h3 className="text-xs font-semibold uppercase text-slate-500">证据边界</h3><dl className="mt-2"><Fact label="来源" value={provenance.source} /><Fact label="观测时间" value={provenance.observed_at ? new Date(String(provenance.observed_at)).toLocaleString("zh-CN") : null} /><Fact label="缺失数据" value={missingData.length ? missingData.join("、") : "无"} /><Fact label="备选方案" value={alternatives.length ? alternatives.map((item) => item.replaceAll("_", " ")).join("、") : null} /></dl></div>
      </div>

      <button onClick={() => setExpanded((value) => !value)} className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-cyan-700">{expanded ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}技术依据</button>
      {expanded && <div className="mt-4 border-t border-slate-200 pt-4"><details><summary className="inline-flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-600"><FileJson className="size-4" />原始决策包</summary><pre className="mt-3 max-h-72 overflow-auto bg-slate-950 p-4 text-xs leading-5 text-slate-200">{JSON.stringify(payload, null, 2)}</pre></details>{payload.action_hash && <div className="mt-4"><p className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-500"><Fingerprint className="size-4" />Action Hash</p><code className="block break-all bg-slate-100 px-3 py-2 text-xs text-slate-700">{String(payload.action_hash)}</code></div>}</div>}
      {deciding && <ApprovalDecisionPanel approval={approval} onClose={() => setDeciding(false)} />}
    </div>
  </article>;
}
