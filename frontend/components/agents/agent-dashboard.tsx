"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { zhCN } from "date-fns/locale";
import {
  Activity,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  ClipboardCheck,
  Cpu,
  DatabaseZap,
  FileJson,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { useMemo, useState } from "react";
import { StatusBadge } from "@/components/ui/status-badge";
import { apiFetch } from "@/lib/api/client";
import type { AgentRun, AgentRunListResponse, AgentRunStatus, AgentTask, TaskListResponse } from "@/lib/api/types";

const STATUS_LABELS: Record<AgentRunStatus, string> = {
  received: "已接收",
  validated: "已校验",
  evidence_collected: "证据已收集",
  analyzed: "分析完成",
  output_validated: "输出已校验",
  planned: "计划完成",
  policy_evaluated: "策略已评估",
  awaiting_approval: "等待审批",
  approved: "已批准",
  executing: "执行中",
  verifying: "验证中",
  succeeded: "成功",
  degraded: "降级完成",
  failed: "失败",
  canceled: "已取消",
};

const ACTIVE_STATUSES = new Set<AgentRunStatus>([
  "received",
  "validated",
  "evidence_collected",
  "analyzed",
  "output_validated",
  "planned",
  "policy_evaluated",
  "approved",
  "executing",
  "verifying",
]);

function relativeTime(value: string | number) {
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return formatDistanceToNow(date, { addSuffix: true, locale: zhCN });
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function displayNumber(value: unknown) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : String(value ?? "0");
}

function MetricCard({ label, value, icon: Icon, tone }: { label: string; value: number; icon: typeof Activity; tone: string }) {
  return <div className="panel p-5"><div className="flex items-center justify-between"><div><p className="text-sm font-medium text-slate-500">{label}</p><p className="mt-2 text-3xl font-bold">{value}</p></div><div className={`grid size-11 place-items-center rounded-lg ${tone}`}><Icon className="size-5" /></div></div></div>;
}

function RunFacts({ run, onResume, resuming }: { run: AgentRun; onResume: (runId: string) => void; resuming: boolean }) {
  const analysis = asRecord(run.analysis_result);
  const preparation = asRecord(analysis.evidence_preparation);
  const usage = asRecord(run.model_usage);
  const evidence = asArray(asRecord(run.evidence_manifest).evidence);
  const selected = Number(preparation.selected_evidence_count ?? evidence.length);
  const original = Number(preparation.original_evidence_count ?? evidence.length);
  const summary = String(analysis.executive_summary ?? asRecord(run.state.plan).summary ?? "当前运行尚未生成业务摘要。");
  const missingData = asArray(analysis.missing_data).map((item) => asRecord(item)).filter((item) => item.field);
  const warnings = asArray(analysis.warnings).map(String);
  const history = run.state.history ?? [];
  const failureStage = run.status === "failed" ? history.at(-2)?.status ?? history.at(-1)?.status : undefined;

  return <div className="border-t border-slate-200 bg-slate-50 px-5 py-5 lg:px-6">
    {run.status === "failed" && <div className="mb-5 flex flex-col gap-3 rounded-lg border border-rose-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between"><div className="flex gap-3"><ShieldX className="mt-0.5 size-5 shrink-0 text-rose-600" /><div><p className="text-sm font-bold text-slate-950">模型建议未生成，系统未伪造成功结果</p><p className="mt-1 text-xs leading-5 text-slate-600">错误类别：{run.error_category ?? "UnknownError"}。已保留工作流、证据清单和审计记录；Amazon 写入能力保持关闭。</p></div></div><button onClick={() => onResume(run.run_id)} disabled={resuming || run.state.retryable === false} className="inline-flex min-h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-slate-300 bg-white px-3 text-xs font-bold text-slate-800 disabled:opacity-40">{resuming ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}受控重试</button></div>}
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]">
      <div>
        <p className="text-xs font-semibold uppercase text-slate-500">业务摘要</p>
        <p className="mt-2 text-sm leading-6 text-slate-800">{summary}</p>
        {(missingData.length > 0 || warnings.length > 0) && <div className="mt-4 border-l-2 border-amber-400 pl-3 text-sm text-amber-900">
          {missingData.slice(0, 3).map((item) => <p key={String(item.field)}>缺失：{String(item.field)} · {String(item.reason ?? "待补充")}</p>)}
          {warnings.slice(0, 2).map((item) => <p key={item}>{item}</p>)}
        </div>}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
        <div><dt className="text-xs text-slate-500">生成方式</dt><dd className="mt-1 font-medium text-slate-800">{run.generation_method === "model" ? "结构化模型" : "确定性代码"}</dd></div>
        <div><dt className="text-xs text-slate-500">工作流类别</dt><dd className="mt-1 font-medium text-slate-800">{run.workflow_class.replaceAll("_", " ")}</dd></div>
        <div><dt className="text-xs text-slate-500">证据覆盖</dt><dd className="mt-1 font-medium text-slate-800">{selected} / {original}</dd></div>
        <div><dt className="text-xs text-slate-500">模型</dt><dd className="mt-1 truncate font-medium text-slate-800">{run.model ?? String(usage.model ?? "不适用")}</dd></div>
        <div><dt className="text-xs text-slate-500">Token</dt><dd className="mt-1 font-medium text-slate-800">{displayNumber(usage.total_tokens ?? run.token_usage)}</dd></div>
        <div><dt className="text-xs text-slate-500">估算成本 USD</dt><dd className="mt-1 font-medium text-slate-800">{Number(run.estimated_cost || 0).toFixed(4)}</dd></div>
        {run.status === "failed" && <><div><dt className="text-xs text-slate-500">失败阶段</dt><dd className="mt-1 font-medium text-rose-700">{failureStage ? STATUS_LABELS[failureStage] : "未知"}</dd></div><div><dt className="text-xs text-slate-500">错误类别</dt><dd className="mt-1 font-medium text-rose-700">{run.error_category ?? "UnknownError"}</dd></div></>}
      </dl>
    </div>
    <div className="mt-5 border-t border-slate-200 pt-4">
      <p className="text-xs font-semibold uppercase text-slate-500">状态记录</p>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-600">{history.map((item, index) => <span key={`${item.status}-${index}`}><strong className="font-semibold text-slate-800">{STATUS_LABELS[item.status]}</strong> · {new Date(item.at).toLocaleString("zh-CN")}</span>)}</div>
    </div>
    <details className="mt-4 border-t border-slate-200 pt-3">
      <summary className="inline-flex cursor-pointer items-center gap-2 text-sm font-medium text-slate-600"><FileJson className="size-4" />原始技术数据</summary>
      <pre className="mt-3 max-h-80 overflow-auto bg-slate-950 p-4 text-xs leading-5 text-slate-200">{JSON.stringify({ analysis_result: run.analysis_result, state: run.state, evidence_preparation: preparation }, null, 2)}</pre>
    </details>
  </div>;
}

function AgentRunRow({ run, onResume, resuming }: { run: AgentRun; onResume: (runId: string) => void; resuming: boolean }) {
  const [open, setOpen] = useState(false);
  return <article className="border-b border-slate-100 last:border-0">
    <button onClick={() => setOpen((value) => !value)} className="grid w-full gap-3 px-5 py-4 text-left transition hover:bg-slate-50 sm:grid-cols-[minmax(0,1.4fr)_150px_140px_120px_24px] sm:items-center lg:px-6" aria-expanded={open}>
      <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><p className="font-semibold text-slate-900">{run.workflow_name}</p><span className={`rounded-full px-2 py-0.5 text-xs font-medium ${run.generation_method === "model" ? "bg-cyan-50 text-cyan-700" : "bg-slate-100 text-slate-700"}`}>{run.generation_method === "model" ? "Model" : "Deterministic"}</span></div><p className="mt-1 truncate font-mono text-xs text-slate-500">{run.run_id}</p></div>
      <div><p className="text-xs text-slate-500">发起人</p><p className="mt-1 truncate text-sm font-medium text-slate-700">{run.actor_id}</p></div>
      <StatusBadge value={run.status} />
      <div className="text-sm text-slate-500 sm:text-right">{relativeTime(run.updated_at)}<p className="mt-1 text-xs">{run.state.history?.length ?? 0} 次流转</p></div>
      {open ? <ChevronUp className="size-4 text-slate-400" /> : <ChevronDown className="size-4 text-slate-400" />}
    </button>
    {open && <RunFacts run={run} onResume={onResume} resuming={resuming} />}
  </article>;
}

function RunSection({ title, icon: Icon, runs, action, error, loading, onResume, resumingRunId }: { title: string; icon: typeof Bot; runs: AgentRun[]; action: React.ReactNode; error?: Error | null; loading: boolean; onResume: (runId: string) => void; resumingRunId?: string }) {
  return <section className="panel mt-6 overflow-hidden">
    <div className="flex flex-col gap-4 border-b border-slate-200 p-5 sm:flex-row sm:items-center sm:justify-between lg:px-6">
      <div className="flex items-center gap-3"><Icon className="size-5 text-slate-500" /><h2 className="font-semibold text-slate-950">{title}</h2></div>
      {action}
    </div>
    {error && <div className="border-b border-rose-100 bg-rose-50 px-6 py-3 text-sm text-rose-700">{error.message}</div>}
    {loading ? <div className="grid min-h-36 place-items-center"><LoaderCircle className="size-6 animate-spin text-cyan-600" /></div> : runs.length ? runs.map((run) => <AgentRunRow key={run.run_id} run={run} onResume={onResume} resuming={resumingRunId === run.run_id} />) : <div className="grid min-h-36 place-items-center p-6 text-sm text-slate-500">暂无运行记录</div>}
  </section>;
}

function InfrastructureTaskRow({ task }: { task: AgentTask }) {
  return <div className="grid gap-3 border-b border-slate-100 px-5 py-3 text-sm last:border-0 sm:grid-cols-[minmax(0,1.4fr)_120px_120px_120px] sm:items-center lg:px-6">
    <div className="min-w-0"><p className="truncate font-medium text-slate-800">{task.action}</p><p className="mt-1 truncate font-mono text-xs text-slate-500">{task.task_id}</p></div>
    <span className="text-slate-600">{task.queue_name}</span><StatusBadge value={task.status} /><span className="text-slate-500 sm:text-right">{relativeTime(task.updated_at || task.created_at)}</span>
  </div>;
}

export function AgentDashboard() {
  const queryClient = useQueryClient();
  const refreshInterval = Number(process.env.NEXT_PUBLIC_REFRESH_INTERVAL_MS ?? 5000);
  const runsQuery = useQuery({ queryKey: ["agent-runs"], queryFn: () => apiFetch<AgentRunListResponse>("/api/agent-runs?limit=100"), refetchInterval: refreshInterval });
  const tasksQuery = useQuery({ queryKey: ["infrastructure-tasks"], queryFn: () => apiFetch<TaskListResponse>("/api/tasks?limit=30"), refetchInterval: refreshInterval });
  const invalidate = async () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["agent-runs"] }),
    queryClient.invalidateQueries({ queryKey: ["approvals"] }),
  ]);
  const analyzeBrief = useMutation({
    mutationFn: () => apiFetch("/api/agent-runs/operations-brief-analysis", {
      method: "POST",
      headers: { "Idempotency-Key": `operations-brief-${crypto.randomUUID()}` },
      body: JSON.stringify({ locale: "zh-CN" }),
    }),
    onSuccess: invalidate,
  });
  const generateActions = useMutation({
    mutationFn: () => apiFetch("/api/agent-runs/operations-recommendations", { method: "POST", body: JSON.stringify({ max_issues: 20 }) }),
    onSuccess: invalidate,
  });
  const resume = useMutation({
    mutationFn: (runId: string) => apiFetch(`/api/agent-runs/${runId}/resume`, { method: "POST" }),
    onSuccess: invalidate,
  });
  const runs = runsQuery.data?.items ?? [];
  const advisorRuns = runs.filter((run) => run.generation_method === "model");
  const deterministicRuns = runs.filter((run) => run.generation_method === "deterministic");
  const metrics = useMemo(() => ({
    active: runs.filter((run) => ACTIVE_STATUSES.has(run.status)).length,
    approval: runs.filter((run) => run.status === "awaiting_approval").length,
    succeeded: runs.filter((run) => run.status === "succeeded").length,
    degraded: runs.filter((run) => run.status === "degraded").length,
    failed: runs.filter((run) => ["failed", "canceled"].includes(run.status)).length,
  }), [runs]);
  const allAdvisorsFailed = advisorRuns.length > 0 && advisorRuns.every((run) => run.status === "failed");
  const deterministicSucceeded = deterministicRuns.filter((run) => run.status === "succeeded").length;
  const failureCategories = Array.from(new Set(advisorRuns.map((run) => run.error_category).filter(Boolean))).join("、");

  const refreshButton = <button onClick={() => runsQuery.refetch()} className="grid size-10 shrink-0 place-items-center rounded-lg border border-slate-300 bg-white text-slate-600" aria-label="刷新运行记录" title="刷新运行记录"><RefreshCw className={`size-4 ${runsQuery.isFetching ? "animate-spin" : ""}`} /></button>;

  return <div>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
      <MetricCard label="处理中" value={metrics.active} icon={Activity} tone="bg-blue-50 text-blue-600" />
      <MetricCard label="等待审批" value={metrics.approval} icon={ShieldCheck} tone="bg-amber-50 text-amber-600" />
      <MetricCard label="成功" value={metrics.succeeded} icon={CheckCircle2} tone="bg-emerald-50 text-emerald-600" />
      <MetricCard label="安全降级" value={metrics.degraded} icon={TriangleAlert} tone="bg-orange-50 text-orange-600" />
      <MetricCard label="失败或取消" value={metrics.failed} icon={ShieldX} tone="bg-rose-50 text-rose-600" />
    </div>

    {allAdvisorsFailed && <section className="mt-6 flex flex-col gap-4 rounded-lg border border-amber-300 bg-amber-50 p-5 lg:flex-row lg:items-center lg:justify-between"><div className="flex gap-3"><TriangleAlert className="mt-0.5 size-6 shrink-0 text-amber-700" /><div><h2 className="font-bold text-slate-950">结构化模型建议当前不可用</h2><p className="mt-1 text-sm leading-6 text-slate-700">所有 {advisorRuns.length} 次模型运行均失败{failureCategories ? `：${failureCategories}` : ""}。系统没有使用自由文本回退，也没有执行副作用。</p><p className="mt-1 text-xs text-slate-600">确定性计算、异常队列、Policy、HITL、人工执行和验证仍可使用；当前已有 {deterministicSucceeded} 次确定性运行成功。</p></div></div><span className="shrink-0 rounded-lg bg-white px-3 py-2 text-xs font-bold text-amber-800 ring-1 ring-amber-300">安全失败模式生效</span></section>}

    <RunSection
      title="AI Advisors"
      icon={Sparkles}
      runs={advisorRuns}
      loading={runsQuery.isLoading}
      error={analyzeBrief.error}
      onResume={(runId) => resume.mutate(runId)}
      resumingRunId={resume.variables}
      action={<div className="flex w-full gap-2 sm:w-auto">{refreshButton}<button disabled={analyzeBrief.isPending} onClick={() => analyzeBrief.mutate()} className="inline-flex min-h-10 flex-1 items-center justify-center gap-2 rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50 sm:flex-none">{analyzeBrief.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}生成运营简报分析</button></div>}
    />

    <RunSection
      title="确定性运营动作"
      icon={ClipboardCheck}
      runs={deterministicRuns}
      loading={runsQuery.isLoading}
      error={generateActions.error}
      onResume={(runId) => resume.mutate(runId)}
      resumingRunId={resume.variables}
      action={<button disabled={generateActions.isPending} onClick={() => generateActions.mutate()} className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50 sm:w-auto">{generateActions.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Cpu className="size-4" />}生成待审批运营动作</button>}
    />

    <section className="panel mt-6 overflow-hidden">
      <div className="flex items-center justify-between border-b border-slate-200 p-5 lg:px-6"><div className="flex items-center gap-3"><DatabaseZap className="size-5 text-slate-500" /><h2 className="font-semibold text-slate-950">同步与基础设施任务</h2></div></div>
      {tasksQuery.data?.items.length ? tasksQuery.data.items.map((task) => <InfrastructureTaskRow key={task.task_id} task={task} />) : <div className="grid min-h-32 place-items-center text-sm text-slate-500">暂无基础设施任务</div>}
    </section>
  </div>;
}
