"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Activity,
  ArrowUpRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clipboard,
  Clock3,
  Database,
  Download,
  FileCheck2,
  Filter,
  ListChecks,
  Pause,
  Play,
  RefreshCw,
  ShieldAlert,
  ShieldX,
  Square,
  ThumbsDown,
  ThumbsUp,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api/client";
import type { OperationalIssueDetailResponse, OperationalWorkItem, WorkItemCustomerCaseContext, WorkItemListResponse, WorkloadSummary } from "@/lib/api/types";

type View = "today" | "waiting" | "verifying" | "active" | "mine" | "unassigned" | "due_today" | "awaiting_approval" | "blocked" | "verification_failed";
type BulkOperation = "assign" | "set_due_at" | "acknowledge" | "start" | "block" | "unblock" | "handoff";

const views: Array<{ key: View; label: string }> = [
  { key: "today", label: "今天必须处理" },
  { key: "waiting", label: "等待审批 / 外部条件" },
  { key: "verifying", label: "等待结果验证" },
  { key: "active", label: "全部待办" },
  { key: "mine", label: "我的任务" },
  { key: "unassigned", label: "待认领" },
  { key: "due_today", label: "今日到期" },
  { key: "awaiting_approval", label: "等待审批" },
  { key: "blocked", label: "数据阻断" },
  { key: "verification_failed", label: "验证失败" },
];

const statusLabels: Record<string, string> = {
  open: "待认领", assigned: "已分配", in_progress: "处理中", blocked: "已阻断",
  awaiting_approval: "等待审批", verifying: "验证中", completed: "已完成", canceled: "已取消",
};
const severityLabels: Record<string, string> = { critical: "紧急", high: "高", medium: "中", low: "低" };
const operationLabels: Record<BulkOperation, string> = {
  assign: "批量分配", set_due_at: "设置截止时间", acknowledge: "批量认领", start: "批量开始",
  block: "批量阻断", unblock: "批量解除阻断", handoff: "批量交接",
};
const workItemPageSize = 50;

function dateText(value: string | null | undefined) {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function isOverdue(item: OperationalWorkItem) {
  return !["completed", "canceled"].includes(item.status) && new Date(item.due_at).getTime() < Date.now();
}

function sourceLabel(evidence: Record<string, unknown>) {
  if (evidence.verified_amazon_data === true) return "Amazon API 已验证";
  const source = String(evidence.source ?? evidence.data_source ?? "internal_record");
  if (source.includes("mock")) return "Mock Amazon";
  if (source.includes("csv")) return "CSV 导入";
  if (source.includes("manual")) return "人工录入";
  if (source.includes("estimate") || source.includes("deterministic")) return "系统估算";
  return "内部记录";
}

function nextStep(item: OperationalWorkItem) {
  if (item.status === "open") return "认领任务并核对证据新鲜度。";
  if (item.status === "assigned") return "开始处理并记录事实核查结果。";
  if (item.status === "blocked") return `补齐阻断数据：${item.blocked_reason ?? "查看缺失证据"}`;
  if (item.status === "awaiting_approval") return "等待独立审批人复核，发起人不可自审批。";
  if (item.status === "verifying") return "重新同步数据并执行确定性结果验证。";
  if (item.verification_status === "failed") return "检查失败原因并决定重试、回滚或升级。";
  if (item.status === "completed") return "复盘结果指标并保留执行凭证。";
  return "按 SOP 完成当前任务状态。";
}

export function OperationalWorkItemsWorkbench() {
  const [view, setView] = useState<View>("today");
  const [domain, setDomain] = useState("");
  const [team, setTeam] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [operation, setOperation] = useState<BulkOperation>("assign");
  const [assigneeId, setAssigneeId] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [note, setNote] = useState("");
  const [handoffText, setHandoffText] = useState("");
  const [detailItem, setDetailItem] = useState<OperationalWorkItem | null>(null);
  const [deepLinkedWorkItemId, setDeepLinkedWorkItemId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const client = useQueryClient();
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedView = params.get("view") as View | null;
    if (views.some((item) => item.key === requestedView)) {
      setView(requestedView as View);
    }
    setDeepLinkedWorkItemId(params.get("work_item"));
  }, []);
  const query = useQuery({
    queryKey: ["work-items", view, domain, team, page],
    queryFn: () => {
      const params = new URLSearchParams({ view, page: String(page), page_size: String(workItemPageSize) });
      if (domain) params.set("domain", domain);
      if (team) params.set("team", team);
      return apiFetch<WorkItemListResponse>(`/api/work-items?${params}`);
    },
    refetchInterval: 15000,
  });
  const workload = useQuery({ queryKey: ["work-items-workload"], queryFn: () => apiFetch<WorkloadSummary>("/api/work-items/workload"), refetchInterval: 15000 });
  const deepLinkedWorkItem = useQuery({
    queryKey: ["work-item-deep-link", deepLinkedWorkItemId],
    queryFn: () => apiFetch<OperationalWorkItem>(`/api/work-items/${encodeURIComponent(deepLinkedWorkItemId!)}`),
    enabled: Boolean(deepLinkedWorkItemId),
  });
  const learning = useQuery({ queryKey: ["learning-metrics"], queryFn: () => apiFetch<{ feedback: Record<string, string | number | boolean | null>; labor: { completed_sessions: number; average_active_touch_seconds?: number | null; average_cycle_seconds?: number | null } }>("/api/learning/metrics"), refetchInterval: 30000 });
  const detail = useQuery({
    queryKey: ["work-item-detail", detailItem?.operational_issue_id],
    queryFn: () => apiFetch<OperationalIssueDetailResponse>(`/api/issues/${detailItem?.operational_issue_id}/detail`),
    enabled: Boolean(detailItem),
  });
  const bulk = useMutation({
    mutationFn: () => apiFetch<{ total: number }>("/api/work-items/bulk", {
      method: "POST",
      body: JSON.stringify({
        work_item_ids: selected,
        operation,
        assignee_id: assigneeId || undefined,
        due_at: dueAt ? new Date(dueAt).toISOString() : undefined,
        note: note || undefined,
      }),
    }),
    onSuccess: async () => {
      setSelected([]);
      setNote("");
      await Promise.all([query.refetch(), workload.refetch()]);
    },
  });
  const handoff = useMutation({
    mutationFn: () => apiFetch<{ items: OperationalWorkItem[] }>("/api/work-items/handoff-packet"),
    onSuccess: (data) => {
      const text = data.items.map((item) => `${item.severity.toUpperCase()} ${item.seller_sku} | ${item.title} | ${statusLabels[item.status]} | 截止 ${dateText(item.due_at)} | ${item.handoff_note ?? item.blocked_reason ?? item.expected_outcome}`).join("\n");
      setHandoffText(text || "当前没有未完成任务");
    },
  });
  const items = query.data?.items ?? [];
  const selectedAll = items.length > 0 && items.every((item) => selected.includes(item.id));
  const counts = useMemo(() => ({
    overdue: items.filter(isOverdue).length,
    critical: items.filter((item) => item.severity === "critical").length,
    blocked: items.filter((item) => item.status === "blocked").length,
  }), [items]);
  useEffect(() => {
    if (query.data && query.data.pages > 0 && page > query.data.pages) {
      setPage(query.data.pages);
      setSelected([]);
    }
  }, [page, query.data]);
  useEffect(() => {
    if (deepLinkedWorkItem.data) {
      setDetailItem((current) => current ?? deepLinkedWorkItem.data ?? null);
    }
  }, [deepLinkedWorkItem.data]);

  function changePage(nextPage: number) {
    setPage(nextPage);
    setSelected([]);
  }

  function toggleAll() {
    setSelected(selectedAll ? [] : items.map((item) => item.id));
  }

  function toggle(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }

  function submitBulk() {
    if (!selected.length) return;
    if (["assign", "handoff"].includes(operation) && !assigneeId.trim()) return;
    if (["block", "handoff"].includes(operation) && !note.trim()) return;
    bulk.mutate();
  }

  return <div className="space-y-5">
    <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
      <div><p className="eyebrow">Operations control tower</p><h1 className="mt-2 text-3xl font-bold tracking-tight text-slate-950">运营任务工作台</h1><p className="mt-2 text-sm text-slate-600">{query.data?.generated_at ? `更新于 ${dateText(query.data.generated_at)}` : "加载任务队列"}</p></div>
      <div className="flex flex-wrap gap-2"><button onClick={() => query.refetch()} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-semibold"><RefreshCw className="size-4" />刷新</button><button onClick={() => handoff.mutate()} className="inline-flex items-center gap-2 rounded-lg bg-slate-950 px-3 py-2 text-sm font-semibold text-white"><Clipboard className="size-4" />生成交接包</button></div>
    </header>

    <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Kpi label="当前队列" value={String(query.data?.total ?? 0)} icon={<ListChecks className="size-4" />} />
      <Kpi label="有效信号率" value={percentText(learning.data?.feedback.precision)} icon={<ShieldAlert className="size-4 text-rose-600" />} />
      <Kpi label="平均主动处理" value={durationText(learning.data?.labor.average_active_touch_seconds)} icon={<Activity className="size-4 text-cyan-700" />} />
      <Kpi label="平均闭环周期" value={durationText(learning.data?.labor.average_cycle_seconds)} icon={<Clock3 className="size-4 text-amber-600" />} />
    </section>

    <section className="panel overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-slate-200 p-4 lg:flex-row lg:items-center lg:justify-between"><div className="flex min-w-0 items-center gap-2 overflow-x-auto pb-1">{views.map((item) => <button key={item.key} onClick={() => { setView(item.key); setPage(1); setSelected([]); }} className={`shrink-0 rounded-lg px-3 py-2 text-xs font-semibold ${view === item.key ? "bg-slate-950 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>{item.label}</button>)}</div><div className="flex items-center gap-2"><Filter className="size-4 text-slate-400" /><input value={domain} onChange={(event) => { setDomain(event.target.value); setPage(1); setSelected([]); }} placeholder="领域" className="w-24 rounded-lg border border-slate-300 px-2.5 py-2 text-xs" /><input value={team} onChange={(event) => { setTeam(event.target.value); setPage(1); setSelected([]); }} placeholder="团队" className="w-24 rounded-lg border border-slate-300 px-2.5 py-2 text-xs" /></div></div>

      <div className="border-b border-slate-200 bg-slate-50/70 p-3"><div className="flex flex-col gap-3 xl:flex-row xl:items-center"><div className="flex items-center gap-2 text-xs font-semibold text-slate-600"><span>{selected.length} 项已选</span><button onClick={toggleAll} className="rounded-md border border-slate-300 bg-white px-2 py-1.5">{selectedAll ? "取消全选" : "全选当前页"}</button></div><select value={operation} onChange={(event) => setOperation(event.target.value as BulkOperation)} className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold"><option value="">选择批量动作</option>{Object.entries(operationLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select><input value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)} placeholder="负责人 ID" className="rounded-lg border border-slate-300 px-3 py-2 text-xs" /><input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs" /><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="阻断/交接备注" className="min-w-40 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-xs" /><button onClick={submitBulk} disabled={!selected.length || bulk.isPending} className="inline-flex items-center justify-center gap-2 rounded-lg bg-cyan-700 px-3 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:opacity-40"><Check className="size-4" />执行内部协作</button></div>{bulk.error && <p className="mt-2 text-xs text-rose-700">{bulk.error.message}</p>}</div>

      <div className="overflow-x-auto"><table className="w-full min-w-[980px] text-left text-sm"><thead className="border-b border-slate-200 text-xs text-slate-500"><tr><th className="w-12 px-4 py-3"><input type="checkbox" checked={selectedAll} onChange={toggleAll} aria-label="选择当前任务" /></th><th className="px-3 py-3">任务</th><th className="px-3 py-3">范围</th><th className="px-3 py-3">负责人</th><th className="px-3 py-3">状态</th><th className="px-3 py-3">SLA</th><th className="px-3 py-3">结果</th></tr></thead><tbody>{items.map((item) => <tr key={item.id} className="border-b border-slate-100 align-top last:border-0 hover:bg-slate-50"><td className="px-4 py-4"><input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggle(item.id)} aria-label={`选择 ${item.title}`} /></td><td className="max-w-[380px] px-3 py-4"><div className="flex flex-wrap items-center gap-2"><span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${item.severity === "critical" ? "bg-rose-100 text-rose-700" : item.severity === "high" ? "bg-orange-100 text-orange-700" : "bg-slate-100 text-slate-600"}`}>{severityLabels[item.severity]}</span>{item.escalation_level > 0 && <span className="inline-flex items-center gap-1 text-[11px] font-bold text-rose-700"><ArrowUpRight className="size-3" />升级 {item.escalation_level}</span>}</div><button onClick={() => setDetailItem(item)} className="mt-2 flex w-full items-start justify-between gap-2 text-left font-semibold text-slate-950 hover:text-cyan-700"><span>{item.title}</span><ChevronRight className="mt-0.5 size-4 shrink-0" /></button><p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{item.description}</p></td><td className="px-3 py-4 text-xs text-slate-600"><p className="font-semibold text-slate-900">{item.seller_sku}</p><p className="mt-1">{item.marketplace_id}</p><p className="mt-1">{item.domain} · {item.team}</p></td><td className="px-3 py-4 text-xs text-slate-600">{item.assignee_id ?? <span className="font-semibold text-amber-700">待认领</span>}{item.handoff_note && <p className="mt-1 max-w-36 text-slate-400">{item.handoff_note}</p>}</td><td className="px-3 py-4"><span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-700">{statusLabels[item.status] ?? item.status}</span>{item.blocked_reason && <p className="mt-2 max-w-40 text-xs text-rose-700">{item.blocked_reason}</p>}</td><td className="px-3 py-4 text-xs"><p className={isOverdue(item) ? "font-bold text-rose-700" : "text-slate-600"}>{dateText(item.due_at)}</p><p className="mt-1 text-slate-400">{isOverdue(item) ? "已逾期" : item.sla_policy_version}</p></td><td className="px-3 py-4 text-xs text-slate-600"><p>{item.verification_status === "pending" ? "待验证" : item.verification_status}</p><p className="mt-1">{item.expected_outcome}</p></td></tr>)}</tbody></table>{!query.isLoading && items.length === 0 && <div className="grid min-h-40 place-items-center text-sm text-slate-500">当前视图没有任务</div>}</div>
      <QueuePagination page={page} pages={query.data?.pages ?? 0} total={query.data?.total ?? 0} loading={query.isFetching} onPageChange={changePage} />
    </section>

    {detailItem && <WorkItemDetail item={detailItem} detail={detail.data} loading={detail.isLoading} onClose={() => setDetailItem(null)} onUpdated={async () => { await Promise.all([query.refetch(), workload.refetch(), learning.refetch()]); }} />}

    <section className="grid gap-5 xl:grid-cols-[1.25fr_1fr]"><div className="panel overflow-hidden"><div className="flex items-center justify-between border-b border-slate-200 p-4"><h2 className="font-semibold">团队工作量</h2><Users className="size-4 text-slate-400" /></div><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b border-slate-100 text-xs text-slate-500"><tr><th className="px-4 py-3">负责人</th><th className="px-4 py-3">待办</th><th className="px-4 py-3">紧急</th><th className="px-4 py-3">逾期</th><th className="px-4 py-3">阻断</th></tr></thead><tbody>{(workload.data?.assignees ?? []).map((person) => <tr key={person.assignee_id} className="border-b border-slate-100 last:border-0"><td className="px-4 py-3 font-medium">{person.assignee_id === "unassigned" ? "待认领" : person.assignee_id}</td><td className="px-4 py-3">{person.total}</td><td className="px-4 py-3 text-rose-700">{person.critical}</td><td className="px-4 py-3 text-amber-700">{person.overdue}</td><td className="px-4 py-3 text-slate-600">{person.blocked}</td></tr>)}</tbody></table>{!workload.isLoading && !(workload.data?.assignees.length) && <p className="p-5 text-sm text-slate-500">暂无工作量</p>}</div></div><div className="panel p-4"><div className="flex items-center justify-between"><h2 className="font-semibold">班次交接</h2><button onClick={() => handoff.mutate()} disabled={handoff.isPending} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold"><Download className="size-4" />生成</button></div>{handoffText ? <div className="mt-4"><textarea readOnly value={handoffText} className="min-h-40 w-full rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs leading-5" /><button onClick={() => navigator.clipboard?.writeText(handoffText)} className="mt-2 inline-flex items-center gap-2 text-xs font-semibold text-cyan-700"><Clipboard className="size-3" />复制交接内容</button></div> : <p className="mt-4 text-sm text-slate-500">交接包尚未生成</p>}</div></section>
  </div>;
}

function WorkItemDetail({ item, detail, loading, onClose, onUpdated }: { item: OperationalWorkItem; detail?: OperationalIssueDetailResponse; loading: boolean; onClose: () => void; onUpdated: () => Promise<void> }) {
  const [feedbackReason, setFeedbackReason] = useState("threshold_not_applicable");
  const [feedbackNote, setFeedbackNote] = useState("");
  const activities = useQuery({ queryKey: ["learning-activities", item.id], queryFn: () => apiFetch<{ items: Array<{ event_type: string; occurred_at: string }> }>(`/api/learning/activities?work_item_id=${encodeURIComponent(item.id)}`) });
  const authorizations = useQuery({ queryKey: ["execution-authorizations", item.id], queryFn: () => apiFetch<{ items: Array<{ id: string; status: string; risk_level: string; expires_at: string }> }>(`/api/learning/execution-authorizations?work_item_id=${encodeURIComponent(item.id)}`) });
  const caseContext = useQuery({ queryKey: ["work-item-customer-case-context", item.id], queryFn: () => apiFetch<WorkItemCustomerCaseContext>(`/api/work-items/${encodeURIComponent(item.id)}/customer-case-context`) });
  const prepareExecution = useMutation({
    mutationFn: () => {
      const evidence = item.evidence_manifest[0] ?? {};
      return apiFetch("/api/learning/execution-authorizations", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          work_item_id: item.id,
          action_hash: item.action_hash,
          action_type: action?.action_type ?? "review_advertising_action",
          exact_parameters: action?.parameters ?? {},
          before_snapshot: evidence,
          verified_amazon_data: evidence.verified_amazon_data === true,
          data_fresh_seconds: Number(evidence.freshness_seconds ?? 0),
          currency: action?.currency ?? evidence.currency ?? "USD",
          account_currency: action?.currency ?? evidence.currency ?? "USD",
          inventory_status: evidence.inventory_status ?? item.baseline_metrics.inventory_status ?? "unknown",
          contribution_profit: action?.expected_profit_impact ?? item.baseline_metrics.contribution_profit ?? null,
          expires_at: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
        }),
      });
    },
    onSuccess: async () => { await authorizations.refetch(); await onUpdated(); },
  });
  const attributionPlan = useMutation({
    mutationFn: () => {
      const now = new Date();
      const baselineStart = new Date(now.getTime() - 28 * 86400000);
      const observationEnd = new Date(now.getTime() + 7 * 86400000);
      const evidence = item.evidence_manifest[0] ?? {};
      return apiFetch("/api/learning/attribution-plans", {
        method: "POST",
        body: JSON.stringify({
          work_item_id: item.id,
          action_hash: item.action_hash,
          primary_metric: "contribution_profit",
          desired_direction: "increase",
          baseline_start: baselineStart.toISOString(),
          baseline_end: now.toISOString(),
          observation_start: now.toISOString(),
          observation_end: observationEnd.toISOString(),
          currency: action?.currency ?? evidence.currency ?? "USD",
          has_control_group: false,
          minimum_sample: 100,
          guardrail_metrics: ["return_rate", "conversion_rate", "inventory_coverage_days"],
          control_spec: {},
          confounders: [],
        }),
      });
    },
    onSuccess: async () => { await onUpdated(); },
  });
  const activity = useMutation({
    mutationFn: (eventType: string) => apiFetch("/api/learning/activities", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ work_item_id: item.id, event_type: eventType, occurred_at: new Date().toISOString() }) }),
    onSuccess: async () => { await activities.refetch(); await onUpdated(); },
  });
  const feedback = useMutation({
    mutationFn: (input: { correct: boolean; useful: boolean }) => apiFetch(`/api/learning/issues/${encodeURIComponent(item.operational_issue_id)}/feedback`, { method: "POST", body: JSON.stringify({ is_correct: input.correct, is_useful: input.useful, reason_code: input.correct ? "actionable" : feedbackReason, rule_version: String(detail?.issue.issue_type ?? item.domain) + "-operator-v1", note: feedbackNote || null }) }),
    onSuccess: async () => { setFeedbackNote(""); await onUpdated(); },
  });
  const activityItems = activities.data?.items ?? [];
  const lastActivity = activityItems.length ? activityItems[activityItems.length - 1].event_type : null;
  const issue = detail?.issue;
  const action = detail?.recommended_actions.find((candidate) => candidate.id === item.recommended_action_id) ?? detail?.recommended_actions[0];
  const approval = detail?.pending_approval;
  const timeline = [
    { label: "任务创建", at: item.created_at, done: true, detail: `${item.source_type} · ${item.source_id}` },
    { label: "负责人认领", at: item.acknowledged_at, done: Boolean(item.assignee_id), detail: item.assignee_id ?? "尚未认领" },
    { label: "审批复核", at: approval?.decided_at ?? approval?.created_at, done: Boolean(approval), detail: approval ? `${approval.status} · ${approval.request_id}` : "当前动作尚无审批记录" },
    { label: "人工执行", at: issue?.resolved_at ?? issue?.started_at, done: Boolean(issue?.execution_result_id), detail: issue?.execution_result_id ?? "未执行 Amazon 写入" },
    { label: "结果验证", at: item.completed_at ?? item.updated_at, done: item.verification_status !== "pending", detail: `${item.verification_status} · ${item.verification_attempts} 次` },
  ];
  return <section className="panel overflow-hidden border-cyan-200">
    <div className="flex items-start justify-between gap-4 border-b border-slate-200 p-5"><div><p className="eyebrow">Operational task detail</p><h2 className="mt-1 text-lg font-bold text-slate-950">{item.title}</h2><p className="mt-1 text-xs text-slate-500">{item.id} · Action hash {item.action_hash.slice(0, 12)}...</p></div><button onClick={onClose} className="grid size-9 shrink-0 place-items-center rounded-lg border border-slate-300" aria-label="关闭任务详情"><X className="size-4" /></button></div>
    {loading ? <div className="grid min-h-48 place-items-center text-sm text-slate-500">加载任务事实...</div> : <div className="grid xl:grid-cols-[1.2fr_0.8fr]">
      <div className="space-y-6 p-5 xl:border-r xl:border-slate-200">
        <div className="grid gap-3 sm:grid-cols-3"><DetailFact label="业务影响" value={Object.keys(item.baseline_metrics).length ? Object.entries(item.baseline_metrics).map(([key, value]) => `${key}: ${String(value)}`).join(" · ") : "尚无可量化影响"} /><DetailFact label="当前状态" value={statusLabels[item.status] ?? item.status} /><DetailFact label="负责人 / SLA" value={`${item.assignee_id ?? "待认领"} · ${dateText(item.due_at)}`} /></div>
        {caseContext.data?.cluster && <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-bold text-cyan-800">客服 Case 根因证据</p><p className="mt-1 text-sm font-semibold">{caseContext.data.cluster.seller_sku} · {caseContext.data.cluster.root_cause_code}</p><p className="mt-1 text-xs text-slate-600">{caseContext.data.related_case_count} 个关联 Case · {caseContext.data.cluster.team} · {caseContext.data.cluster.status}</p></div><Link href={caseContext.data.case_ids[0] ? `/customer-cases?case=${encodeURIComponent(caseContext.data.case_ids[0])}` : "/customer-cases"} className="rounded-lg border border-cyan-700 bg-white px-3 py-2 text-xs font-bold text-cyan-800">打开 Case 360</Link></div><p className={`mt-2 text-xs font-semibold ${caseContext.data.cluster.verified_amazon_data ? "text-emerald-700" : "text-amber-700"}`}>{caseContext.data.cluster.verified_amazon_data ? "证据来源已由 Amazon 验证" : "证据包含 Mock / 未验证来源，不得宣称为 Amazon 已验证"}</p></div>}
        <div><h3 className="flex items-center gap-2 text-sm font-bold"><Database className="size-4 text-cyan-700" />证据与来源</h3><div className="mt-3 grid gap-2 sm:grid-cols-2">{item.evidence_manifest.map((evidence, index) => <div key={index} className="rounded-lg border border-slate-200 p-3 text-xs"><div className="flex items-center justify-between gap-2"><span className={evidence.verified_amazon_data === true ? "font-bold text-emerald-700" : "font-bold text-amber-700"}>{sourceLabel(evidence)}</span><span className="text-slate-400">{dateText(String(evidence.observed_at ?? issue?.data_freshness_at ?? ""))}</span></div><p className="mt-2 break-all text-slate-600">{String(evidence.snapshot_id ?? evidence.signal_id ?? evidence.resource_id ?? evidence.summary ?? `Evidence ${index + 1}`)}</p></div>)}{!item.evidence_manifest.length && <p className="text-xs text-amber-700">没有可展示的证据清单，任务应保持降级。</p>}</div></div>
        <div className="grid gap-4 lg:grid-cols-2"><div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4"><h3 className="flex items-center gap-2 text-sm font-bold text-emerald-800"><CheckCircle2 className="size-4" />建议动作</h3><p className="mt-2 text-sm font-semibold text-slate-900">{action?.title ?? item.title}</p><p className="mt-1 text-xs leading-5 text-slate-600">{action?.description ?? item.description}</p><p className="mt-2 text-xs text-emerald-800">预期：{action?.expected_outcome ?? item.expected_outcome}</p></div><div className="rounded-lg border border-rose-200 bg-rose-50 p-4"><h3 className="flex items-center gap-2 text-sm font-bold text-rose-800"><ShieldX className="size-4" />禁止动作</h3><p className="mt-2 text-xs leading-5 text-slate-700">自动退款、支付撤销、自主取消、审批绕过、跨租户操作和任意 Amazon 写入永久禁止。当前建议 automation_eligible={String(action?.automation_eligible ?? false)}。</p></div></div>
        <div><h3 className="flex items-center gap-2 text-sm font-bold"><FileCheck2 className="size-4 text-blue-700" />执行凭证与验证</h3><div className="mt-3 grid gap-3 sm:grid-cols-2"><DetailFact label="执行结果" value={issue?.execution_result_id ?? "等待人工执行凭证"} /><DetailFact label="验证结果" value={Object.keys(item.result_metrics).length ? JSON.stringify(item.result_metrics) : `${item.verification_status}，尝试 ${item.verification_attempts} 次`} /></div></div>
        {item.domain === "advertising" && <div className="rounded-lg border border-cyan-200 bg-cyan-50/60 p-4"><h3 className="text-sm font-bold text-cyan-900">广告行动闸门</h3><p className="mt-1 text-xs leading-5 text-cyan-800">先登记行动前归因计划，再执行人工确认。Mock 或数据未验证时，系统会阻止授权。</p><div className="mt-3 flex flex-wrap gap-2"><button onClick={() => attributionPlan.mutate()} disabled={attributionPlan.isPending} className="inline-flex items-center gap-2 rounded-lg border border-cyan-700 bg-white px-3 py-2 text-xs font-bold text-cyan-800"><FileCheck2 className="size-3.5" />登记归因计划</button><button onClick={() => prepareExecution.mutate()} disabled={prepareExecution.isPending || item.evidence_manifest[0]?.verified_amazon_data !== true} className="inline-flex items-center gap-2 rounded-lg bg-cyan-700 px-3 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:opacity-40"><ShieldAlert className="size-3.5" />准备人工确认执行</button></div>{authorizations.data?.items.length ? <p className="mt-2 text-xs text-slate-600">最近授权：{authorizations.data.items[0].status} · {authorizations.data.items[0].risk_level} · 到期 {dateText(authorizations.data.items[0].expires_at)}</p> : <p className="mt-2 text-xs text-slate-500">尚无执行授权</p>}{attributionPlan.isSuccess && <p className="mt-2 text-xs font-semibold text-emerald-700">行动前归因计划已登记，观察窗口开始前不会宣称收益。</p>}{prepareExecution.isSuccess && <p className="mt-2 text-xs font-semibold text-emerald-700">执行授权已准备，仍需按风险等级完成审批或人工确认。</p>}{attributionPlan.error && <p className="mt-2 text-xs text-rose-700">{attributionPlan.error.message}</p>}{prepareExecution.error && <p className="mt-2 text-xs text-rose-700">{prepareExecution.error.message}</p>}</div>}
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-lg border border-slate-200 p-4"><h3 className="flex items-center gap-2 text-sm font-bold"><Clock3 className="size-4 text-cyan-700" />实际处理时间</h3><div className="mt-3 flex flex-wrap gap-2">{!lastActivity && <button onClick={() => activity.mutate("started")} disabled={activity.isPending} className="inline-flex items-center gap-2 rounded-lg bg-cyan-700 px-3 py-2 text-xs font-bold text-white"><Play className="size-3.5" />开始处理</button>}{["started", "resumed"].includes(lastActivity ?? "") && <><button onClick={() => activity.mutate("paused")} disabled={activity.isPending} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-2 text-xs font-bold"><Pause className="size-3.5" />暂停</button><button onClick={() => activity.mutate("completed")} disabled={activity.isPending} className="inline-flex items-center gap-2 rounded-lg bg-slate-950 px-3 py-2 text-xs font-bold text-white"><Square className="size-3.5" />完成计时</button></>}{lastActivity === "paused" && <button onClick={() => activity.mutate("resumed")} disabled={activity.isPending} className="inline-flex items-center gap-2 rounded-lg bg-cyan-700 px-3 py-2 text-xs font-bold text-white"><Play className="size-3.5" />继续处理</button>}{lastActivity === "completed" && <span className="inline-flex items-center gap-2 text-xs font-bold text-emerald-700"><CheckCircle2 className="size-4" />已记录主动处理时间</span>}</div>{activity.error && <p className="mt-2 text-xs text-rose-700">{activity.error.message}</p>}</div>
          <div className="rounded-lg border border-slate-200 p-4"><h3 className="text-sm font-bold">运营有效性反馈</h3><div className="mt-3 grid gap-2"><select value={feedbackReason} onChange={(event) => setFeedbackReason(event.target.value)} className="rounded-lg border border-slate-300 px-3 py-2 text-xs"><option value="not_a_real_issue">并非真实问题</option><option value="stale_or_missing_data">数据过期 / 缺失</option><option value="threshold_not_applicable">阈值不适用</option><option value="duplicate_issue">重复问题</option><option value="planned_promotion_or_seasonality">促销 / 季节因素</option><option value="already_handled_externally">已在外部处理</option><option value="impact_too_low">业务影响太低</option><option value="recommendation_not_executable">建议不可执行</option><option value="accepted_planned_risk">计划性风险</option></select><input value={feedbackNote} onChange={(event) => setFeedbackNote(event.target.value)} placeholder="核验备注" className="rounded-lg border border-slate-300 px-3 py-2 text-xs" /><div className="flex gap-2"><button onClick={() => feedback.mutate({ correct: true, useful: true })} disabled={feedback.isPending} className="inline-flex items-center gap-2 rounded-lg bg-emerald-700 px-3 py-2 text-xs font-bold text-white"><ThumbsUp className="size-3.5" />有效信号</button><button onClick={() => feedback.mutate({ correct: false, useful: false })} disabled={feedback.isPending} className="inline-flex items-center gap-2 rounded-lg border border-rose-300 px-3 py-2 text-xs font-bold text-rose-700"><ThumbsDown className="size-3.5" />无效 / 噪声</button></div></div>{feedback.error && <p className="mt-2 text-xs text-rose-700">{feedback.error.message}</p>}</div>
        </div>
        <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-4"><p className="text-xs font-bold text-cyan-800">下一步</p><p className="mt-1 text-sm font-semibold text-slate-900">{nextStep(item)}</p></div>
      </div>
      <div className="p-5"><h3 className="flex items-center gap-2 text-sm font-bold"><Activity className="size-4 text-slate-600" />SOP 时间线</h3><div className="mt-5 space-y-0">{timeline.map((event, index) => <div key={event.label} className="relative flex gap-3 pb-6 last:pb-0"><div className={`relative z-10 mt-0.5 grid size-6 shrink-0 place-items-center rounded-full ${event.done ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-400"}`}>{event.done ? <Check className="size-3.5" /> : <Clock3 className="size-3.5" />}</div>{index < timeline.length - 1 && <span className="absolute left-[11px] top-6 h-full w-px bg-slate-200" />}<div><p className="text-sm font-semibold text-slate-900">{event.label}</p><p className="mt-1 text-xs text-slate-500">{event.detail}</p><p className="mt-1 text-[11px] text-slate-400">{dateText(event.at)}</p></div></div>)}</div></div>
    </div>}
  </section>;
}

function DetailFact({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-slate-200 p-3"><p className="text-xs font-semibold text-slate-500">{label}</p><p className="mt-2 text-sm leading-5 text-slate-900">{value}</p></div>;
}

function Kpi({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return <div className="panel flex items-center justify-between p-4"><div><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-2xl font-bold text-slate-950">{value}</p></div><span className="grid size-9 place-items-center rounded-lg bg-slate-100 text-slate-700">{icon}</span></div>;
}

function QueuePagination({ page, pages, total, loading, onPageChange }: { page: number; pages: number; total: number; loading: boolean; onPageChange: (page: number) => void }) {
  return <div className="flex items-center justify-between border-t border-slate-200 px-4 py-3 text-xs text-slate-600"><span>共 {total} 项 · 第 {pages === 0 ? 0 : page} / {pages} 页</span><div className="flex gap-2"><button type="button" aria-label="上一页运营任务" title="上一页" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={loading || page <= 1} className="grid size-9 place-items-center rounded-lg border border-slate-300 text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"><ChevronLeft className="size-4" /></button><button type="button" aria-label="下一页运营任务" title="下一页" onClick={() => onPageChange(Math.min(pages, page + 1))} disabled={loading || pages === 0 || page >= pages} className="grid size-9 place-items-center rounded-lg border border-slate-300 text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"><ChevronRight className="size-4" /></button></div></div>;
}

function percentText(value: unknown) {
  if (value === null || value === undefined) return "待标注";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(1)}%` : "待标注";
}

function durationText(value: number | null | undefined) {
  if (value === null || value === undefined) return "待测量";
  if (value < 60) return `${value} 秒`;
  return `${Math.round(value / 60)} 分钟`;
}
