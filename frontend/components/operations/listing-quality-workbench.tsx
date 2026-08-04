"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Clock3, Database, FilePlus2, GitCompare, LineChart, Play, RefreshCw, Send, ShieldCheck, Undo2 } from "lucide-react";
import { apiFetch } from "@/lib/api/client";
import type { ListingExperiment, ListingExperimentListResponse, ListingExperimentMutationResponse, ListingQualitySnapshot, ListingSnapshotListResponse } from "@/lib/api/types";

const statusLabels: Record<string, string> = {
  critical: "严重", poor: "较差", fair: "一般", good: "良好",
  awaiting_approval: "等待审批", approved: "已批准", observing: "观察中", completed: "已完成",
  directional_improvement: "方向性改善", confounded: "变量干扰", inconclusive: "证据不足", rollback_recommended: "建议回滚", kept: "保留", rollback_planned: "已决定回滚",
};
const statusStyles: Record<string, string> = {
  critical: "bg-rose-100 text-rose-700", poor: "bg-orange-100 text-orange-700", fair: "bg-amber-100 text-amber-700", good: "bg-emerald-100 text-emerald-700",
  awaiting_approval: "bg-amber-100 text-amber-800", approved: "bg-blue-100 text-blue-700", observing: "bg-violet-100 text-violet-700",
  completed: "bg-emerald-100 text-emerald-700", inconclusive: "bg-slate-100 text-slate-700", rollback_recommended: "bg-rose-100 text-rose-700",
  directional_improvement: "bg-cyan-100 text-cyan-700", confounded: "bg-orange-100 text-orange-700", kept: "bg-emerald-100 text-emerald-700", rollback_planned: "bg-orange-100 text-orange-700",
};
const controlFields = [
  ["ad_spend", "广告花费"], ["ad_traffic_share", "广告流量占比"], ["cpc", "CPC"],
  ["price", "价格"], ["discount_rate", "折扣率"], ["buy_box_share", "Buy Box 占比"],
  ["sellable_units", "可售库存"], ["out_of_stock_hours", "缺货时长"], ["natural_rank", "自然排名"],
  ["promotion_active", "促销状态"], ["seasonality_key", "季节性"], ["competitor_change", "竞品变化"],
] as const;
const reasonLabels: Record<string, string> = {
  control_variables_incomplete: "控制变量不完整，不能归因于 Listing 修改",
  experiment_sample_below_minimum: "样本量低于 200 sessions",
  business_outcome_metrics_incomplete: "转化率或退货率不完整",
  conversion_drop_exceeds_guardrail: "转化率下降超过 10% 护栏",
  return_rate_increase_exceeds_guardrail: "退货率上升超过 2 个百分点",
};

function number(value: unknown, digits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits }) : String(value);
}

function date(value?: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-";
}

function Badge({ value }: { value: string }) {
  return <span className={`rounded-full px-2.5 py-1 text-xs font-bold ${statusStyles[value] ?? "bg-slate-100 text-slate-700"}`}>{statusLabels[value] ?? value}</span>;
}

function Metric({ label, value, tone = "text-slate-950" }: { label: string; value: string; tone?: string }) {
  return <div className="rounded-xl bg-slate-50 p-3"><p className="text-xs text-slate-500">{label}</p><p className={`mt-1 text-xl font-bold ${tone}`}>{value}</p></div>;
}

function ControlMatrix({ before, after }: { before: ListingQualitySnapshot; after?: ListingQualitySnapshot }) {
  const complete = controlFields.filter(([key]) => before.baseline_metrics[key] !== null && before.baseline_metrics[key] !== undefined).length;
  return <section className="panel overflow-hidden"><div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 p-4"><div><h3 className="font-bold">实验控制变量</h3><p className="mt-1 text-xs text-slate-500">广告、价格、库存、Buy Box、排名、促销、季节和竞品变化必须同时观察。</p></div><span className={`rounded-full px-2.5 py-1 text-xs font-bold ${complete === controlFields.length ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-800"}`}>{complete}/{controlFields.length} 完整</span></div><div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left text-xs"><thead className="border-b border-slate-200 text-slate-500"><tr><th className="px-4 py-3">控制变量</th><th className="px-4 py-3">修改前</th><th className="px-4 py-3">修改后</th><th className="px-4 py-3">状态</th></tr></thead><tbody>{controlFields.map(([key, label]) => { const beforeValue = before.baseline_metrics[key]; const afterValue = after?.baseline_metrics[key]; const missing = beforeValue === null || beforeValue === undefined || (after && (afterValue === null || afterValue === undefined)); const changed = Boolean(after) && !missing && String(beforeValue) !== String(afterValue); return <tr key={key} className="border-b border-slate-100 last:border-0"><td className="px-4 py-3 font-semibold text-slate-800">{label}</td><td className="px-4 py-3 text-slate-600">{beforeValue === null || beforeValue === undefined ? "缺失" : String(beforeValue)}</td><td className="px-4 py-3 text-slate-600">{after ? (afterValue === null || afterValue === undefined ? "缺失" : String(afterValue)) : "等待后快照"}</td><td className="px-4 py-3"><span className={missing ? "font-bold text-amber-700" : changed ? "font-bold text-orange-700" : "font-bold text-emerald-700"}>{missing ? "不完整" : changed ? "需判断漂移" : after ? "稳定" : "基线已记录"}</span></td></tr>; })}</tbody></table></div></section>;
}

function parseJson(value: string, label: string) {
  try {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    return parsed as Record<string, unknown>;
  } catch {
    throw new Error(`${label}必须是 JSON 对象，例如 {"title":"New title"}`);
  }
}

function SnapshotDetail({ snapshot, onCreated }: { snapshot: ListingQualitySnapshot; onCreated: () => void }) {
  const [changes, setChanges] = useState(JSON.stringify({ title: `${snapshot.title} - revised` }, null, 2));
  const [observationEnd, setObservationEnd] = useState(() => new Date(Date.now() + 14 * 86400000).toISOString().slice(0, 16));
  const [notice, setNotice] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: async () => apiFetch<ListingExperimentMutationResponse>("/api/listing-quality/experiments", {
      method: "POST",
      body: JSON.stringify({
        before_snapshot_id: snapshot.id,
        proposed_changes: parseJson(changes, "建议变更"),
        observation_start: new Date().toISOString(),
        observation_end: new Date(observationEnd).toISOString(),
      }),
    }),
    onSuccess: () => { setNotice("实验已创建并进入审批队列。API 不会直接发布 Listing。"); onCreated(); },
    onError: error => setNotice(error instanceof Error ? error.message : "实验创建失败"),
  });
  return <div className="space-y-5">
    <section className="panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><p className="eyebrow">Listing quality snapshot</p><div className="mt-2 flex flex-wrap items-center gap-3"><h2 className="text-2xl font-bold">{snapshot.seller_sku}</h2><Badge value={snapshot.quality_status} /><span className="text-sm text-slate-500">版本 {snapshot.content_version} · {snapshot.marketplace_id}</span></div><p className="mt-2 text-sm text-slate-600">{snapshot.title || "未提供标题"}</p></div>
        <div className="text-right"><p className="text-4xl font-bold text-slate-950">{number(snapshot.quality_score, 0)}</p><p className="text-xs text-slate-500">质量评分 / 100</p></div>
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-3"><Metric label="数据来源" value={snapshot.source} /><Metric label="置信度" value={`${number(Number(snapshot.confidence) * 100, 0)}%`} /><Metric label="观察时间" value={date(snapshot.source_observed_at)} /></div>
      {(snapshot.missing_data.length > 0 || snapshot.degraded_reason) && <div className="mt-4 flex gap-3 rounded-xl bg-amber-50 p-3 text-sm text-amber-900"><AlertTriangle className="mt-0.5 size-4 shrink-0" /><span>{snapshot.degraded_reason ?? "存在缺失数据"}{snapshot.missing_data.length > 0 ? `：${snapshot.missing_data.join("、")}` : ""}。结论只能作为人工复核依据。</span></div>}
    </section>

    <div className="grid gap-5 xl:grid-cols-[1fr_1fr]">
      <section className="panel p-5"><div className="flex items-center gap-2"><GitCompare className="size-5 text-brand-600" /><h3 className="font-bold">问题与建议</h3></div><div className="mt-4 space-y-3">{snapshot.quality_issues.length === 0 ? <p className="text-sm text-slate-500">当前没有结构化 Listing 问题。</p> : snapshot.quality_issues.map((issue, index) => <div key={`${issue.code}-${index}`} className="rounded-xl border border-rose-200 bg-rose-50/60 p-3"><div className="flex items-center justify-between gap-3"><span className="font-semibold text-slate-900">{issue.section}</span><span className="text-xs font-bold text-rose-700">{issue.severity}</span></div><p className="mt-1 text-sm text-slate-700">{issue.rationale}</p></div>)}</div></section>
      <section className="panel p-5"><div className="flex items-center gap-2"><LineChart className="size-5 text-brand-600" /><h3 className="font-bold">基线业务指标</h3></div><div className="mt-4 grid gap-3 sm:grid-cols-2"><Metric label="Sessions" value={number(snapshot.baseline_metrics.sessions, 0)} /><Metric label="Units ordered" value={number(snapshot.baseline_metrics.units_ordered, 0)} /><Metric label="Conversion rate" value={snapshot.baseline_metrics.conversion_rate == null ? "缺失" : `${number(Number(snapshot.baseline_metrics.conversion_rate) * 100, 2)}%`} /><Metric label="Return rate" value={snapshot.baseline_metrics.return_rate == null ? "缺失" : `${number(Number(snapshot.baseline_metrics.return_rate) * 100, 2)}%`} /></div><div className="mt-4 space-y-2 text-sm">{Object.entries(snapshot.section_scores).map(([key, value]) => <div key={key} className="flex items-center gap-3"><span className="w-24 text-slate-500">{key}</span><div className="h-2 flex-1 rounded-full bg-slate-100"><div className="h-2 rounded-full bg-brand-500" style={{ width: `${Math.min(100, Number(value) * 5)}%` }} /></div><span className="w-10 text-right font-semibold">{value}</span></div>)}</div></section>
    </div>

    <ControlMatrix before={snapshot} />

    <section className="panel p-5"><div className="flex items-center gap-2"><FilePlus2 className="size-5 text-brand-600" /><h3 className="font-bold">创建 Listing 实验</h3></div><p className="mt-1 text-sm text-slate-600">输入精确变更，提交后由独立审批人复核。观察期结束后必须用新的 Listing 快照比较转换率和退货率。</p><div className="mt-4 grid gap-4 lg:grid-cols-[1fr_220px_auto] lg:items-end"><label className="text-sm font-medium text-slate-700">建议变更（JSON）<textarea value={changes} onChange={event => setChanges(event.target.value)} rows={5} className="mt-2 w-full rounded-xl border border-slate-300 p-3 font-mono text-xs" /></label><label className="text-sm font-medium text-slate-700">观察结束<input type="datetime-local" value={observationEnd} onChange={event => setObservationEnd(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm" /></label><button onClick={() => create.mutate()} disabled={create.isPending} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-semibold text-white disabled:opacity-50"><Send className="size-4" />{create.isPending ? "提交中" : "提交审批"}</button></div>{notice && <p className="mt-3 rounded-xl bg-slate-50 p-3 text-sm text-slate-700">{notice}</p>}</section>
  </div>;
}

function ExperimentCard({ experiment, snapshots, onChanged }: { experiment: ListingExperiment; snapshots: ListingQualitySnapshot[]; onChanged: () => void }) {
  const [actualChanges, setActualChanges] = useState(JSON.stringify(experiment.proposed_changes, null, 2));
  const [evidence, setEvidence] = useState("");
  const [reason, setReason] = useState("");
  const [afterSnapshotId, setAfterSnapshotId] = useState(experiment.after_snapshot_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const mutate = useMutation({
    mutationFn: async (input: { path: string; body: Record<string, unknown> }) => apiFetch<unknown>(`/api/listing-quality/experiments/${experiment.id}/${input.path}`, { method: "POST", body: JSON.stringify(input.body) }),
    onSuccess: () => { setError(null); onChanged(); },
    onError: value => setError(value instanceof Error ? value.message : "操作失败"),
  });
  const relatedSnapshots = snapshots.filter(item => item.seller_sku === experiment.seller_sku && item.marketplace_id === experiment.marketplace_id && item.id !== experiment.before_snapshot_id);
  const beforeSnapshot = snapshots.find(item => item.id === experiment.before_snapshot_id);
  const afterSnapshot = snapshots.find(item => item.id === experiment.after_snapshot_id);
  function recordExecution() { try { mutate.mutate({ path: "execution", body: { action_hash: experiment.action_hash, actual_changes: parseJson(actualChanges, "实际变更"), execution_evidence: { seller_central_reference: evidence.trim(), platform_write_executed: false }, notes: "Operator recorded manual Seller Central execution." } }); } catch (value) { setError(value instanceof Error ? value.message : "实际变更格式错误"); } }
  return <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-2"><Badge value={experiment.status} /><span className="text-xs text-slate-500">{experiment.seller_sku} · 版本快照 {experiment.before_snapshot_id.slice(0, 8)}</span></div><h3 className="mt-3 text-lg font-bold">Listing 内容实验</h3><p className="mt-1 text-sm text-slate-600">{experiment.expected_outcome}</p></div><div className="text-right"><p className="text-xs text-slate-500">实验 ID</p><code className="text-xs text-slate-700">{experiment.id.slice(0, 12)}</code></div></div>
    <div className="mt-4 grid gap-3 sm:grid-cols-5"><Metric label="基线转化率" value={experiment.baseline_metrics.conversion_rate == null ? "缺失" : `${number(Number(experiment.baseline_metrics.conversion_rate) * 100, 2)}%`} /><Metric label="基线退货率" value={experiment.baseline_metrics.return_rate == null ? "缺失" : `${number(Number(experiment.baseline_metrics.return_rate) * 100, 2)}%`} /><Metric label="观察区间" value={`${date(experiment.observation_start)} - ${date(experiment.observation_end)}`} /><Metric label="执行状态" value={statusLabels[experiment.execution_status] ?? experiment.execution_status} /><Metric label="归因结论" value={statusLabels[experiment.effect_metrics.attribution_status] ?? "待评估"} /></div>
    <div className="mt-4 grid gap-4 lg:grid-cols-2"><div className="rounded-xl bg-slate-50 p-4"><p className="text-xs font-bold uppercase tracking-wider text-slate-500">Proposed changes</p><pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words text-xs text-slate-700">{JSON.stringify(experiment.proposed_changes, null, 2)}</pre></div><div className="rounded-xl bg-slate-50 p-4"><p className="text-xs font-bold uppercase tracking-wider text-slate-500">Effect metrics</p>{Object.keys(experiment.effect_metrics).length === 0 ? <p className="mt-2 text-sm text-slate-500">观察期结束后生成。</p> : <dl className="mt-2 space-y-1 text-sm">{Object.entries(experiment.effect_metrics).map(([key, value]) => <div key={key} className="flex justify-between gap-3"><dt className="text-slate-500">{key}</dt><dd className="font-semibold">{value}</dd></div>)}</dl>}</div></div>
    {beforeSnapshot && <div className="mt-4"><ControlMatrix before={beforeSnapshot} after={afterSnapshot} /></div>}
    {experiment.evaluation_reasons.length > 0 && <div className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-900"><AlertTriangle className="mr-2 inline size-4" />{experiment.evaluation_reasons.map(reason => reasonLabels[reason] ?? (reason.startsWith("confounded_") ? `控制变量发生漂移：${reason.replace("confounded_", "").replace("_drift", "").replace("_change", "")}` : reason)).join("；")}</div>}
    {experiment.status === "awaiting_approval" && <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl bg-amber-50 p-4 text-sm text-amber-900"><ShieldCheck className="size-5" /><span>精确变更已提交审批。请由独立 Approver 在审批工作台批准后再执行。</span><a href="/approvals" className="font-bold underline">打开审批工作台</a></div>}
    {experiment.status === "approved" && <div className="mt-4 grid gap-4 rounded-xl border border-blue-200 bg-blue-50/60 p-4 lg:grid-cols-[1fr_1fr_auto] lg:items-end"><label className="text-sm font-medium text-slate-700">实际变更（JSON）<textarea value={actualChanges} onChange={event => setActualChanges(event.target.value)} rows={4} className="mt-2 w-full rounded-xl border border-slate-300 bg-white p-3 font-mono text-xs" /></label><label className="text-sm font-medium text-slate-700">Seller Central 执行凭证<input value={evidence} onChange={event => setEvidence(event.target.value)} placeholder="工单号、截图编号或操作记录 ID" className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm" /></label><button onClick={recordExecution} disabled={mutate.isPending || !evidence.trim()} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-semibold text-white disabled:opacity-50"><Play className="size-4" />记录人工执行</button></div>}
    {experiment.execution_status === "executed" && <div className="mt-4 grid gap-4 rounded-xl border border-violet-200 bg-violet-50/60 p-4 sm:grid-cols-[1fr_auto] sm:items-end"><label className="text-sm font-medium text-slate-700">选择观察期结束后的 Listing 快照<select value={afterSnapshotId} onChange={event => setAfterSnapshotId(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm"><option value="">请选择同 SKU 快照</option>{relatedSnapshots.map(snapshot => <option key={snapshot.id} value={snapshot.id}>{date(snapshot.calculated_at)} · v{snapshot.content_version} · {snapshot.id.slice(0, 8)}</option>)}</select></label><button onClick={() => mutate.mutate({ path: "evaluate", body: { after_snapshot_id: afterSnapshotId, evaluated_at: new Date().toISOString() } })} disabled={mutate.isPending || !afterSnapshotId} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 text-sm font-semibold text-white disabled:opacity-50"><LineChart className="size-4" />评估实验</button></div>}
    {experiment.execution_status === "verified" && !experiment.decision && <div className="mt-4 grid gap-4 rounded-xl border border-slate-200 bg-slate-50 p-4 lg:grid-cols-[1fr_auto_auto] lg:items-end"><label className="text-sm font-medium text-slate-700">保留/回滚说明<input value={reason} onChange={event => setReason(event.target.value)} placeholder="说明指标变化与业务决定" className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm" /></label><button onClick={() => mutate.mutate({ path: "decision", body: { decision: "keep", reason } })} disabled={mutate.isPending || !reason.trim()} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl border border-emerald-600 px-4 text-sm font-semibold text-emerald-700 disabled:opacity-50"><CheckCircle2 className="size-4" />保留</button><button onClick={() => mutate.mutate({ path: "decision", body: { decision: "rollback", reason } })} disabled={mutate.isPending || !reason.trim()} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-orange-600 px-4 text-sm font-semibold text-white disabled:opacity-50"><Undo2 className="size-4" />决定回滚</button></div>}
    {experiment.decision && <div className="mt-4 flex items-start gap-3 rounded-xl bg-slate-900 p-4 text-sm text-white"><CheckCircle2 className="mt-0.5 size-5 text-emerald-400" /><div><p className="font-semibold">已记录决定：{experiment.decision === "keep" ? "保留实验结果" : "安排人工回滚"}</p><p className="mt-1 text-slate-300">{experiment.decision_reason} · {date(experiment.decision_at)}</p>{experiment.decision === "rollback" && <p className="mt-2 text-amber-300">平台写入仍为关闭状态，请按审批后的变更手册人工回滚并保留凭证。</p>}</div></div>}
    {error && <p className="mt-3 rounded-xl bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
  </section>;
}

function SyncedListingCapture({ onCreated }: { onCreated: () => void }) {
  const [connectionId, setConnectionId] = useState("");
  const [sellerSku, setSellerSku] = useState("");
  const [marketplaceId, setMarketplaceId] = useState("ATVPDKIKX0DER");
  const [notice, setNotice] = useState<string | null>(null);
  const capture = useMutation({
    mutationFn: () => apiFetch<{ snapshot: ListingQualitySnapshot }>("/api/listing-quality/snapshots/from-listing", {
      method: "POST",
      body: JSON.stringify({ connection_id: connectionId.trim(), seller_sku: sellerSku.trim(), marketplace_id: marketplaceId.trim(), calculated_at: new Date().toISOString() }),
    }),
    onSuccess: result => { setNotice(`已生成 ${result.snapshot.seller_sku} 的质量快照。`); onCreated(); },
    onError: error => setNotice(error instanceof Error ? error.message : "无法从同步 Listing 生成快照"),
  });
  return <section className="panel p-5"><div className="flex items-center gap-2"><Database className="size-5 text-brand-600" /><h2 className="font-bold">从已同步 Listing 开始</h2></div><p className="mt-1 text-sm text-slate-600">读取当前租户最近一次同步的 Listing 内容和平台元数据，自动保留来源、新鲜度与 Amazon 验证标记。</p><div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr_1fr_auto] lg:items-end"><label className="text-sm font-medium text-slate-700">Connection ID<input value={connectionId} onChange={event => setConnectionId(event.target.value)} placeholder="Amazon connection" className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm" /></label><label className="text-sm font-medium text-slate-700">Seller SKU<input value={sellerSku} onChange={event => setSellerSku(event.target.value)} placeholder="SKU-001" className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm" /></label><label className="text-sm font-medium text-slate-700">Marketplace<input value={marketplaceId} onChange={event => setMarketplaceId(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 text-sm" /></label><button onClick={() => capture.mutate()} disabled={capture.isPending || !connectionId.trim() || !sellerSku.trim() || !marketplaceId.trim()} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-brand-600 px-4 text-sm font-semibold text-white disabled:opacity-50"><RefreshCw className="size-4" />{capture.isPending ? "读取中" : "生成快照"}</button></div>{notice && <p className="mt-3 rounded-xl bg-slate-50 p-3 text-sm text-slate-700">{notice}</p>}</section>;
}

export function ListingQualityWorkbench() {
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const snapshots = useQuery({ queryKey: ["listing-snapshots"], queryFn: () => apiFetch<ListingSnapshotListResponse>("/api/listing-quality/snapshots"), refetchInterval: 15000 });
  const experiments = useQuery({ queryKey: ["listing-experiments"], queryFn: () => apiFetch<ListingExperimentListResponse>("/api/listing-quality/experiments"), refetchInterval: 10000 });
  const items = snapshots.data?.items ?? [];
  const selected = items.find(item => item.id === selectedId) ?? items[0];
  const activeExperiments = (experiments.data?.items ?? []).filter(item => !["kept", "rollback_planned", "completed", "directional_improvement", "confounded", "inconclusive"].includes(item.status));
  const rollbackCount = (experiments.data?.items ?? []).filter(item => item.status === "rollback_recommended" || item.status === "rollback_planned").length;
  function refresh() { void client.invalidateQueries({ queryKey: ["listing-snapshots"] }); void client.invalidateQueries({ queryKey: ["listing-experiments"] }); }
  return <div className="space-y-6">
    <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between"><div><p className="eyebrow">Content operations / controlled experiments</p><h1 className="mt-2 text-3xl font-bold tracking-tight text-slate-950">Listing 质量与实验</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">从结构化问题到人工执行和结果决策的单一工作台。每个建议都绑定快照、审批、执行凭证和观察指标。</p></div><div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-900"><ShieldCheck className="size-4" />Amazon 写入关闭 · 人工执行记录</div></header>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric label="质量快照" value={String(items.length)} /><Metric label="待复核快照" value={String(items.filter(item => item.quality_status !== "good").length)} tone="text-orange-700" /><Metric label="活动实验" value={String(activeExperiments.length)} tone="text-violet-700" /><Metric label="回滚提醒" value={String(rollbackCount)} tone="text-rose-700" /></div>
    <SyncedListingCapture onCreated={refresh} />
    <div className="grid gap-6 xl:grid-cols-[320px_1fr]"><aside className="space-y-3"><div className="flex items-center justify-between"><h2 className="font-bold">快照队列</h2><button title="刷新数据" onClick={refresh} className="grid size-9 place-items-center rounded-lg border border-slate-300 bg-white text-slate-600 hover:bg-slate-50"><RefreshCw className="size-4" /></button></div>{snapshots.isLoading && <p className="text-sm text-slate-500">加载快照…</p>}{!snapshots.isLoading && items.length === 0 && <div className="rounded-xl border border-dashed border-slate-300 p-5 text-sm text-slate-500">暂无 Listing 质量快照。请从已同步 Listing 生成。</div>}{items.map(snapshot => <button key={snapshot.id} onClick={() => setSelectedId(snapshot.id)} className={`w-full rounded-xl border p-4 text-left transition ${selected?.id === snapshot.id ? "border-brand-500 bg-brand-50 shadow-sm" : "border-slate-200 bg-white hover:border-slate-400"}`}><div className="flex items-center justify-between gap-2"><span className="font-bold text-slate-950">{snapshot.seller_sku}</span><Badge value={snapshot.quality_status} /></div><div className="mt-3 flex justify-between text-xs text-slate-500"><span>质量 {number(snapshot.quality_score, 0)}</span><span>v{snapshot.content_version}</span></div><p className="mt-2 truncate text-xs text-slate-500">{snapshot.quality_issues[0]?.rationale ?? "无结构化问题"}</p></button>)}</aside><main className="min-w-0">{selected ? <SnapshotDetail snapshot={selected} onCreated={refresh} /> : <div className="grid min-h-80 place-items-center rounded-2xl border border-dashed border-slate-300 bg-white text-sm text-slate-500"><Database className="mb-2 size-6" />等待 Listing 快照</div>}</main></div>
    <section className="space-y-3"><div className="flex items-center gap-2"><Clock3 className="size-5 text-brand-600" /><h2 className="font-bold">实验生命周期</h2><span className="text-sm text-slate-500">审批 → 人工执行 → 观察 → 决策</span></div>{experiments.isLoading && <p className="text-sm text-slate-500">加载实验…</p>}{!experiments.isLoading && (experiments.data?.items.length ?? 0) === 0 && <div className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">还没有 Listing 实验。</div>}{experiments.data?.items.map(experiment => <ExperimentCard key={experiment.id} experiment={experiment} snapshots={items} onChanged={refresh} />)}</section>
  </div>;
}
