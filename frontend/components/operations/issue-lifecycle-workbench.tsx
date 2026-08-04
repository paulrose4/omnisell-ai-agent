"use client";

import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Clock3, Database, ExternalLink, Fingerprint, ShieldCheck, TrendingDown } from "lucide-react";
import { useState } from "react";
import { apiFetch } from "@/lib/api/client";
import type {
  IssueStatus,
  OperationalIssue,
  OperationalIssueApprovalResponse,
  OperationalIssueDetailResponse,
  RecommendedAction,
} from "@/lib/api/types";

const filters: IssueStatus[] = ["open", "acknowledged", "in_progress", "awaiting_approval", "approved_for_manual_execution", "verifying", "verification_failed", "resolved", "dismissed", "reopened"];
const transitions: Partial<Record<IssueStatus, IssueStatus[]>> = {
  open: ["acknowledged", "in_progress", "dismissed"],
  reopened: ["acknowledged", "in_progress", "dismissed"],
  acknowledged: ["in_progress", "dismissed"],
  in_progress: ["dismissed"],
  verification_failed: ["in_progress", "dismissed"],
  resolved: ["reopened"],
};
const statusLabels: Record<IssueStatus, string> = {
  open: "待处理", acknowledged: "已认领", in_progress: "处理中", awaiting_approval: "等待审批",
  approved_for_manual_execution: "已批准待人工执行", verifying: "验证中", verification_failed: "验证失败",
  resolved: "已解决", dismissed: "已忽略", reopened: "重新打开",
};
const transitionLabels: Partial<Record<IssueStatus, string>> = {
  acknowledged: "认领", in_progress: "开始处理", dismissed: "忽略", reopened: "重新打开",
};
const severityLabels = { critical: "紧急", high: "高风险", medium: "中风险", low: "低风险" } as const;
const mockDrillEnabled = process.env.NEXT_PUBLIC_MOCK_DRILL_ENABLED === "true";
const sellerCentralUrl = process.env.NEXT_PUBLIC_SELLER_CENTRAL_URL || "https://sellercentral.amazon.com";

function formatNumber(value: string | number | null | undefined, digits = 2) {
  if (value === null || value === undefined || value === "") return "-";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits }) : String(value);
}

function formatMoney(value: string | null | undefined, currency?: string | null) {
  if (value === null || value === undefined) return "-";
  return `${currency ?? ""} ${formatNumber(value)}`.trim();
}

function evidenceValue(action: RecommendedAction, key: string) {
  for (const evidence of action.evidence ?? []) if (evidence[key] !== undefined) return evidence[key];
  return undefined;
}

function ManualExecutionLink({ className }: { className: string }) {
  if (mockDrillEnabled) return <Link href="/mock-seller-central" className={className}>前往 Mock Seller Central</Link>;
  return <a href={sellerCentralUrl} target="_blank" rel="noreferrer" className={className}>打开 Seller Central<ExternalLink className="size-4" /></a>;
}

function IssueDetail({ issueId, onChanged }: { issueId: string; onChanged: () => void }) {
  const [connectionId, setConnectionId] = useState("");
  const detail = useQuery({
    queryKey: ["issue-detail", issueId],
    queryFn: () => apiFetch<OperationalIssueDetailResponse>(`/api/issues/${issueId}/detail`),
    refetchInterval: 5000,
  });
  const connections = useQuery({
    queryKey: ["connections"],
    queryFn: () => apiFetch<{ items: Array<{
      id: string;
      selling_partner_id: string;
      marketplace_ids: string[];
      status: string;
    }> }>("/api/connections"),
  });
  const submit = useMutation({
    mutationFn: ({ actionId, selectedConnectionId, note }: { actionId: string; selectedConnectionId: string; note: string }) => apiFetch<OperationalIssueApprovalResponse>(`/api/issues/${issueId}/approval-requests`, {
      method: "POST",
      body: JSON.stringify({ connection_id: selectedConnectionId, recommended_action_id: actionId, note }),
    }),
    onSuccess: async () => {
      await detail.refetch();
      onChanged();
    },
  });

  if (detail.isLoading) return <div className="mt-5 rounded-2xl bg-slate-50 p-5 text-sm text-slate-500">正在加载证据和推荐动作…</div>;
  if (detail.error || !detail.data) return <div className="mt-5 rounded-2xl bg-rose-50 p-5 text-sm text-rose-700">异常详情加载失败，请刷新后重试。</div>;
  const { issue, recommended_actions: actions, pending_approval: approval } = detail.data;
  const eligibleConnections = (connections.data?.items ?? []).filter(
    item => item.status === "active" && item.marketplace_ids.includes(issue.marketplace_id),
  );
  const selectedConnectionId = eligibleConnections.some(item => item.id === connectionId)
    ? connectionId
    : eligibleConnections.length === 1 ? eligibleConnections[0].id : "";

  function requestApproval(action: RecommendedAction) {
    if (!selectedConnectionId) {
      alert("请先选择服务该 Marketplace 的活动 Amazon 连接。");
      return;
    }
    const note = prompt("提交审批说明（必填）", `请审批 ${issue.seller_sku} 的${action.title}建议，仅允许运营人员在 Seller Central 手工执行。`) ?? "";
    if (!note.trim()) return;
    submit.mutate({ actionId: action.id, selectedConnectionId, note });
  }

  return <div className="mt-5 space-y-4 border-t border-slate-200 pt-5">
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="库存覆盖天数" value={`${formatNumber(String(evidenceValue(actions[0], "days_of_supply") ?? ""))} 天`} />
      <Metric label="总补货周期" value={`${formatNumber(String(evidenceValue(actions[0], "total_lead_time_days") ?? ""), 0)} 天`} />
      <Metric label="建议补货量" value={`${formatNumber(actions[0]?.parameters.recommended_reorder_quantity as string | number | undefined, 0)} 件`} />
      <Metric label="建议运输方式" value={String(actions[0]?.parameters.recommended_shipping_mode ?? "待人工判断")} />
    </div>

    <label className="block max-w-xl text-sm font-semibold">
      审批绑定的 Amazon 连接
      <select
        value={selectedConnectionId}
        onChange={event => setConnectionId(event.target.value)}
        className="mt-2 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 font-normal"
      >
        <option value="">选择服务 {issue.marketplace_id} 的活动连接</option>
        {eligibleConnections.map(item => <option key={item.id} value={item.id}>{item.selling_partner_id} · {item.id}</option>)}
      </select>
      {!connections.isLoading && eligibleConnections.length === 0 && <span className="mt-2 block text-xs font-normal text-rose-700">没有可用于该 Marketplace 的活动 Amazon 连接，当前建议不能提交执行审批。</span>}
    </label>

    <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
      <section className="rounded-2xl border border-slate-200 p-4">
        <div className="flex items-center gap-2"><Database className="size-4 text-brand-600"/><h3 className="font-semibold">证据与数据新鲜度</h3></div>
        <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
          <Info label="SKU" value={issue.seller_sku}/><Info label="Marketplace" value={issue.marketplace_id}/>
          <Info label="数据更新时间" value={new Date(issue.data_freshness_at).toLocaleString("zh-CN")}/>
          <Info label="建议置信度" value={`${formatNumber(Number(issue.confidence) * 100, 0)}%`}/>
          <Info label="数据来源" value={String(evidenceValue(actions[0], "data_source") ?? evidenceValue(actions[0], "inventory_source") ?? "未提供")}/>
          <Info label="Amazon 已验证" value={evidenceValue(actions[0], "verified_amazon_data") === true ? "是" : "否（非 Amazon 已验证数据）"}/>
        </dl>
        {issue.missing_data?.length > 0 && <p className="mt-4 rounded-xl bg-amber-50 p-3 text-xs text-amber-800">缺失数据：{issue.missing_data.join("、")}。缺失信息必须由运营人员复核，系统不会自动执行。</p>}
      </section>

      <section className="rounded-2xl border border-slate-200 p-4">
        <div className="flex items-center gap-2"><TrendingDown className="size-4 text-rose-600"/><h3 className="font-semibold">量化经营影响</h3></div>
        <dl className="mt-3 space-y-3 text-sm">
          <Info label="预计收入影响" value={formatMoney(actions[0]?.expected_revenue_impact, actions[0]?.currency)}/>
          <Info label="预计利润影响" value={formatMoney(actions[0]?.expected_profit_impact, actions[0]?.currency)}/>
          <Info label="预计执行成本" value={formatMoney(actions[0]?.expected_cost_impact, actions[0]?.currency)}/>
        </dl>
      </section>
    </div>

    {actions.map(action => <section key={action.id} className="rounded-2xl border border-blue-200 bg-blue-50/50 p-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div><div className="flex flex-wrap gap-2"><span className="rounded-full bg-rose-100 px-2 py-1 text-xs font-bold text-rose-700">{severityLabels[action.risk_level]}</span><span className="rounded-full bg-white px-2 py-1 text-xs text-slate-600">仅人工执行</span></div><h3 className="mt-3 font-bold">{action.title}</h3><p className="mt-1 text-sm leading-6 text-slate-600">{action.description}</p><p className="mt-2 text-xs text-slate-500">预期结果：{action.expected_outcome}</p></div>
        {issue.status === "in_progress" && action.status === "proposed" && <button onClick={() => requestApproval(action)} disabled={submit.isPending || !selectedConnectionId} className="rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50">{submit.isPending ? "正在提交…" : "提交 HITL 审批"}</button>}
        {issue.status === "approved_for_manual_execution" && <ManualExecutionLink className="inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white" />}
      </div>
      <div className="mt-4 rounded-xl bg-white p-3 text-xs text-slate-600"><strong>批准参数：</strong><code className="ml-2 break-all">{JSON.stringify(action.parameters)}</code></div>
    </section>)}

    {approval && <section className="rounded-2xl border border-amber-300 bg-amber-50 p-4">
      <div className="flex items-center gap-2"><ShieldCheck className="size-5 text-amber-700"/><h3 className="font-bold text-amber-950">审批请求已创建</h3></div>
      <p className="mt-2 text-sm text-amber-900">审批通过后系统仍不会写入 Amazon，只允许运营人员在 Seller Central 手工执行。</p>
      <div className="mt-3 flex items-start gap-2 rounded-xl bg-white p-3 text-xs text-slate-600"><Fingerprint className="mt-0.5 size-4 shrink-0"/><code className="break-all">{String(approval.payload.action_hash ?? "-")}</code></div>
      <Link href="/approvals" className="mt-3 inline-flex rounded-lg bg-amber-700 px-3 py-2 text-xs font-semibold text-white">前往审批工作台</Link>
    </section>}
    {submit.error && <p className="rounded-xl bg-rose-50 p-3 text-sm text-rose-700">{submit.error.message}</p>}
  </div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl bg-slate-50 p-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-lg font-bold">{value}</p></div>;
}

function Info({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between gap-4 border-b border-slate-100 pb-2"><dt className="text-slate-500">{label}</dt><dd className="text-right font-medium text-slate-900">{value}</dd></div>;
}

export function IssueLifecycleWorkbench() {
  const [view, setView] = useState<"active" | "data_quality" | "conflicts" | "history">("active");
  const [status, setStatus] = useState("");
  const [expandedIssueId, setExpandedIssueId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("issue");
  });
  const query = useQuery({
    queryKey: ["issues", view, status],
    queryFn: () => {
      const params = new URLSearchParams();
      if (status) params.set("status", status);
      else if (view === "history") params.set("view", "history");
      return apiFetch<{ items: OperationalIssue[] }>(`/api/issues${params.size ? `?${params}` : ""}`);
    },
    refetchInterval: 5000,
  });

  async function transition(id: string, target: IssueStatus) {
    const note = prompt(target === "dismissed" ? "忽略原因（必填）" : "处理备注") ?? "";
    if (target === "dismissed" && !note.trim()) return;
    try {
      await apiFetch(`/api/issues/${id}`, { method: "PATCH", body: JSON.stringify({ status: target, note }) });
      await query.refetch();
    } catch (error) {
      alert(error instanceof Error ? error.message : "状态更新失败");
    }
  }

  return <div className="space-y-6">
    <div>
      <p className="eyebrow">Operations work queue</p>
      <h1 className="mt-2 text-3xl font-bold">异常生命周期</h1>
      <p className="mt-2 text-sm text-slate-600">先查看证据和推荐动作，再提交正式 HITL 审批；批准后仍由运营人员手工执行。</p>
    </div>
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div className="inline-flex w-full rounded-lg bg-slate-200/70 p-1 sm:w-auto">{([['active','活跃'],['data_quality','数据质量'],['conflicts','信号冲突'],['history','历史']] as const).map(([key, label]) => <button key={key} onClick={() => { setView(key); setStatus(""); }} className={`flex-1 rounded-md px-3 py-2 text-xs font-semibold sm:flex-none ${view === key ? "bg-white text-slate-950 shadow-sm" : "text-slate-600"}`}>{label}</button>)}</div><select value={status} onChange={event => setStatus(event.target.value)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
      <option value="">全部状态</option>
      {filters.map(item => <option key={item} value={item}>{statusLabels[item]}</option>)}
    </select></div>
    <div className="grid gap-4">
      {query.data?.items.filter((item) => view === "data_quality" ? item.issue_type.includes("data_quality") || item.issue_type.startsWith("missing_") : view === "conflicts" ? item.issue_type.includes("conflict") : view === "active" ? !item.issue_type.includes("data_quality") && !item.issue_type.includes("conflict") && !item.issue_type.startsWith("missing_") : true).map(item => <section key={item.id} className="panel p-5">
        <div className="flex flex-wrap justify-between gap-4">
          <div className="max-w-3xl">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded-full px-2 py-1 text-xs font-bold ${item.severity === "critical" || item.severity === "high" ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-700"}`}>{severityLabels[item.severity]}</span>
              <span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">{statusLabels[item.status]}</span>
              <span className="text-xs text-slate-500">优先级 {formatNumber(item.priority_score, 0)}</span>
            </div>
            <h2 className="mt-3 font-bold">{item.title}</h2>
            <p className="mt-1 text-sm text-slate-600">{item.summary}</p>
            <p className="mt-2 text-xs text-slate-500">{item.marketplace_id} · {item.seller_sku} · 置信度 {formatNumber(Number(item.confidence) * 100, 0)}%</p>
            <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
              <span title={item.assignee_id ?? undefined}>负责人：{item.assignee_id ? "已认领" : "未认领"}</span>
              <span>首次响应：{item.acknowledged_at ? new Date(item.acknowledged_at).toLocaleString("zh-CN") : "-"}</span>
              <span>开始处理：{item.started_at ? new Date(item.started_at).toLocaleString("zh-CN") : "-"}</span>
              <span>重开次数：{item.reopened_count ?? 0}</span>
            </div>
          </div>
          <div className="flex flex-wrap items-start gap-2">
            {(transitions[item.status] ?? []).map(target => <button key={target} onClick={() => transition(item.id, target)} className="h-9 rounded-lg border border-slate-300 px-3 text-xs font-semibold">{transitionLabels[target] ?? statusLabels[target]}</button>)}
            <button onClick={() => setExpandedIssueId(expandedIssueId === item.id ? null : item.id)} className="inline-flex h-9 items-center gap-1 rounded-lg bg-slate-950 px-3 text-xs font-semibold text-white">
              {expandedIssueId === item.id ? <ChevronUp className="size-4"/> : <ChevronDown className="size-4"/>}
              {expandedIssueId === item.id ? "收起详情" : "查看证据与建议"}
            </button>
            {item.status === "approved_for_manual_execution" && <ManualExecutionLink className="inline-flex h-9 items-center gap-1 rounded-lg bg-brand-600 px-3 py-2 text-xs font-semibold text-white" />}
          </div>
        </div>
        {item.missing_data?.length > 0 && <p className="mt-4 rounded-xl bg-amber-50 p-3 text-xs text-amber-800">缺失数据：{item.missing_data.join("、")}</p>}
        {item.status === "verifying" && <p className="mt-4 rounded-xl bg-blue-50 p-3 text-xs text-blue-800"><Clock3 className="mr-1 inline size-4"/>已执行，等待 Amazon 数据重新同步并进行确定性验证。</p>}
        {item.status === "verification_failed" && <p className="mt-4 rounded-xl bg-rose-50 p-3 text-xs text-rose-800">同步后异常仍存在，请重新检查 Seller Central 操作。</p>}
        {expandedIssueId === item.id && <IssueDetail issueId={item.id} onChanged={() => query.refetch()}/>}
      </section>)}
      {!query.isLoading && !(query.data?.items.filter((item) => view === "data_quality" ? item.issue_type.includes("data_quality") || item.issue_type.startsWith("missing_") : view === "conflicts" ? item.issue_type.includes("conflict") : view === "active" ? !item.issue_type.includes("data_quality") && !item.issue_type.includes("conflict") && !item.issue_type.startsWith("missing_") : true).length) && <section className="panel grid min-h-40 place-items-center text-sm text-slate-500">当前视图没有异常</section>}
    </div>
  </div>;
}
