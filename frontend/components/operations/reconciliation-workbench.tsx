"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  CheckCircle2,
  Clock3,
  Database,
  Filter,
  LoaderCircle,
  Play,
  RefreshCw,
  Scale,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api/client";
import type { AgentTask, ReconciliationDifference, ReconciliationRun, ReconciliationRunListResponse, TaskListResponse } from "@/lib/api/types";

type Connection = {
  id: string;
  selling_partner_id: string;
  region: string;
  marketplace_ids: string[];
  status: string;
};

type ConnectionList = { total: number; items: Connection[] };

function ValueView({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="text-slate-400">无记录</span>;
  if (typeof value !== "object" || Array.isArray(value)) return <span className="break-words font-mono text-xs text-slate-800">{String(value)}</span>;
  return <dl className="space-y-1.5">{Object.entries(value as Record<string, unknown>).map(([key, item]) => <div key={key} className="grid grid-cols-[minmax(90px,0.8fr)_minmax(0,1fr)] gap-2 text-xs"><dt className="text-slate-500">{key.replaceAll("_", " ")}</dt><dd className="break-words font-mono text-slate-800">{typeof item === "object" ? JSON.stringify(item) : String(item ?? "-")}</dd></div>)}</dl>;
}

function SourceBadges({ run }: { run: ReconciliationRun }) {
  const sourceLabel = run.data_source === "amazon_api" ? "Amazon API" : run.data_source === "mock" ? "Mock" : "来源未知";
  return <div className="flex flex-wrap gap-2 text-xs font-medium">
    <span className={`rounded-full px-2.5 py-1 ${run.verified_amazon_data ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-800"}`}>{run.verified_amazon_data ? "Amazon 已验证" : "未验证 Amazon"}</span>
    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-700">{sourceLabel}</span>
    <span className={`rounded-full px-2.5 py-1 ${run.freshness_status === "fresh" ? "bg-cyan-50 text-cyan-700" : "bg-orange-50 text-orange-700"}`}>{run.freshness_status === "fresh" ? "数据新鲜" : run.freshness_status === "stale" ? "数据过期" : "新鲜度未知"}</span>
  </div>;
}

function DifferenceTable({ items }: { items: ReconciliationDifference[] }) {
  if (!items.length) return <div className="grid min-h-48 place-items-center border-t border-slate-200 text-sm text-slate-500"><div className="text-center"><CheckCircle2 className="mx-auto size-8 text-emerald-500" /><p className="mt-2 font-medium text-slate-700">当前筛选没有差异</p></div></div>;
  return <div className="overflow-x-auto border-t border-slate-200"><table className="min-w-[920px] w-full text-left text-sm"><thead className="bg-slate-50 text-xs text-slate-500"><tr><th className="px-5 py-3 font-semibold">资源</th><th className="px-5 py-3 font-semibold">差异类型</th><th className="w-[32%] px-5 py-3 font-semibold">Amazon 快照</th><th className="w-[32%] px-5 py-3 font-semibold">内部快照</th></tr></thead><tbody className="divide-y divide-slate-100">{items.map((item) => <tr key={item.id} className="align-top hover:bg-slate-50"><td className="px-5 py-4"><p className="font-semibold text-slate-900">{item.resource_id}</p><p className="mt-1 text-xs text-slate-500">{item.resource_type}</p></td><td className="px-5 py-4"><span className="rounded-full bg-rose-50 px-2.5 py-1 text-xs font-medium text-rose-700">{item.difference_type.replaceAll("_", " ")}</span></td><td className="border-l border-slate-100 px-5 py-4"><ValueView value={item.amazon_value} /></td><td className="border-l border-slate-100 px-5 py-4"><ValueView value={item.internal_value} /></td></tr>)}</tbody></table></div>;
}

export function ReconciliationWorkbench() {
  const queryClient = useQueryClient();
  const [connectionId, setConnectionId] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [resourceFilter, setResourceFilter] = useState("all");
  const [differenceFilter, setDifferenceFilter] = useState("all");
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => apiFetch<ConnectionList>("/api/connections") });
  const runs = useQuery({ queryKey: ["reconciliation"], queryFn: () => apiFetch<ReconciliationRunListResponse>("/api/reconciliation"), refetchInterval: 15000 });
  const tasks = useQuery({ queryKey: ["infrastructure-tasks"], queryFn: () => apiFetch<TaskListResponse>("/api/tasks?limit=30"), refetchInterval: 5000 });

  useEffect(() => {
    if (!connectionId) setConnectionId(connections.data?.items.find((item) => item.status === "active")?.id ?? connections.data?.items[0]?.id ?? "");
  }, [connectionId, connections.data]);
  useEffect(() => {
    if (!selectedRunId || !runs.data?.items.some((item) => item.id === selectedRunId)) setSelectedRunId(runs.data?.items[0]?.id ?? "");
  }, [runs.data, selectedRunId]);

  const startRun = useMutation({
    mutationFn: () => apiFetch<{ task_id: string; status: string }>("/api/reconciliation", {
      method: "POST",
      headers: { "Idempotency-Key": `reconciliation-${connectionId}-${crypto.randomUUID()}` },
      body: JSON.stringify({ connection_id: connectionId }),
    }),
    onSuccess: async () => Promise.all([
      queryClient.invalidateQueries({ queryKey: ["reconciliation"] }),
      queryClient.invalidateQueries({ queryKey: ["infrastructure-tasks"] }),
    ]),
  });
  const investigate = useMutation({
    mutationFn: (runId: string) => apiFetch<{ run_id: string; status: string }>("/api/agent-runs/reconciliation-investigation", {
      method: "POST",
      headers: { "Idempotency-Key": `reconciliation-investigation-${runId}-${crypto.randomUUID()}` },
      body: JSON.stringify({ reconciliation_run_id: runId, locale: "zh-CN" }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent-runs"] }),
  });
  const activeTask: AgentTask | undefined = tasks.data?.items.find((task) => task.task_id === startRun.data?.task_id);

  useEffect(() => {
    if (activeTask && ["succeeded", "failed", "dead_letter"].includes(activeTask.status)) {
      queryClient.invalidateQueries({ queryKey: ["reconciliation"] });
    }
  }, [activeTask?.status, queryClient]);

  const selectedRun = runs.data?.items.find((item) => item.id === selectedRunId) ?? runs.data?.items[0];
  const resourceTypes = useMemo(() => Array.from(new Set(selectedRun?.differences.map((item) => item.resource_type) ?? [])).sort(), [selectedRun]);
  const differenceTypes = useMemo(() => Array.from(new Set(selectedRun?.differences.map((item) => item.difference_type) ?? [])).sort(), [selectedRun]);
  const filtered = (selectedRun?.differences ?? []).filter((item) => (resourceFilter === "all" || item.resource_type === resourceFilter) && (differenceFilter === "all" || item.difference_type === differenceFilter));
  const summary = selectedRun?.summary ?? {};

  return <div className="space-y-6">
    <header><p className="eyebrow">Amazon reconciliation</p><h1 className="mt-2 text-2xl font-bold text-slate-950 lg:text-3xl">每日对账</h1></header>

    <section className="panel overflow-hidden">
      <div className="grid gap-4 p-5 md:grid-cols-[minmax(260px,1fr)_auto] md:items-end lg:px-6">
        <label className="block"><span className="text-xs font-semibold text-slate-500">Amazon 连接</span><select value={connectionId} onChange={(event) => setConnectionId(event.target.value)} className="mt-2 h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900"><option value="">选择连接</option>{connections.data?.items.map((item) => <option key={item.id} value={item.id}>{item.selling_partner_id} · {item.region} · {item.marketplace_ids.join(", ")}</option>)}</select></label>
        <div className="flex gap-2"><button onClick={() => runs.refetch()} className="grid size-11 place-items-center rounded-lg border border-slate-300 bg-white text-slate-600" aria-label="刷新对账记录" title="刷新对账记录"><RefreshCw className={`size-4 ${runs.isFetching ? "animate-spin" : ""}`} /></button><button disabled={!connectionId || startRun.isPending} onClick={() => startRun.mutate()} className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white disabled:opacity-50 md:flex-none">{startRun.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}{runs.data?.items.length ? "重新运行对账" : "开始对账"}</button></div>
      </div>
      {(startRun.error || connections.error) && <div className="border-t border-rose-100 bg-rose-50 px-6 py-3 text-sm text-rose-700">{(startRun.error ?? connections.error)?.message}</div>}
      {startRun.data && <div className={`border-t px-6 py-3 text-sm ${activeTask?.status === "failed" || activeTask?.status === "dead_letter" ? "border-rose-100 bg-rose-50 text-rose-800" : "border-cyan-100 bg-cyan-50 text-cyan-800"}`}><p>任务 {startRun.data.task_id} · {activeTask?.status ?? startRun.data.status}</p>{activeTask?.error && <p className="mt-1">{activeTask.error}</p>}</div>}
    </section>

    {runs.isLoading ? <div className="grid min-h-72 place-items-center"><LoaderCircle className="size-7 animate-spin text-cyan-600" /></div> : runs.error ? <section className="panel grid min-h-56 place-items-center p-6 text-center text-rose-700"><div><TriangleAlert className="mx-auto size-8" /><p className="mt-2">{runs.error.message}</p></div></section> : !runs.data?.items.length ? <section className="panel grid min-h-64 place-items-center p-8 text-center"><div><Scale className="mx-auto size-10 text-slate-300" /><p className="mt-3 font-semibold text-slate-800">暂无对账记录</p><p className="mt-1 text-sm text-slate-500">选择 Amazon 连接后开始第一次只读对账。</p></div></section> : <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div className="panel p-4"><p className="text-xs text-slate-500">差异总数</p><p className="mt-2 text-2xl font-bold text-slate-950">{String(summary.difference_count ?? selectedRun?.differences.length ?? 0)}</p></div>
        <div className="panel p-4"><p className="text-xs text-slate-500">Amazon 订单</p><p className="mt-2 text-2xl font-bold text-slate-950">{String(summary.amazon_order_count ?? 0)}</p></div>
        <div className="panel p-4"><p className="text-xs text-slate-500">内部订单</p><p className="mt-2 text-2xl font-bold text-slate-950">{String(summary.internal_order_count ?? 0)}</p></div>
        <div className="panel p-4"><p className="text-xs text-slate-500">Amazon 库存 SKU</p><p className="mt-2 text-2xl font-bold text-slate-950">{String(summary.amazon_inventory_sku_count ?? 0)}</p></div>
      </div>

      <section className="panel overflow-hidden">
        <div className="grid lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="border-b border-slate-200 bg-slate-50 lg:border-b-0 lg:border-r"><div className="border-b border-slate-200 px-4 py-3 text-xs font-semibold text-slate-500">最近运行</div>{runs.data.items.map((run) => <button key={run.id} onClick={() => { setSelectedRunId(run.id); setResourceFilter("all"); setDifferenceFilter("all"); }} className={`w-full border-b border-slate-200 px-4 py-4 text-left ${run.id === selectedRun?.id ? "bg-white" : "hover:bg-white/70"}`}><div className="flex items-center justify-between gap-2"><span className={`text-xs font-semibold ${run.status === "matched" ? "text-emerald-700" : "text-rose-700"}`}>{run.status === "matched" ? "一致" : "需人工复核"}</span><span className="text-xs text-slate-500">{run.differences.length} 项</span></div><p className="mt-2 text-xs text-slate-500">{new Date(run.started_at).toLocaleString("zh-CN")}</p></button>)}</aside>
          <div className="min-w-0">
            {selectedRun && <><div className="space-y-4 p-5 lg:px-6"><div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between"><div><div className="flex items-center gap-2"><Database className="size-5 text-slate-500" /><h2 className="font-semibold text-slate-950">Run {selectedRun.id.slice(0, 8)}</h2></div><div className="mt-3"><SourceBadges run={selectedRun} /></div><p className="mt-3 text-xs text-slate-500"><Clock3 className="mr-1 inline size-3.5" />观测：{selectedRun.amazon_observed_at ? new Date(selectedRun.amazon_observed_at).toLocaleString("zh-CN") : "未知"} · 采集：{selectedRun.amazon_collected_at ? new Date(selectedRun.amazon_collected_at).toLocaleString("zh-CN") : "未知"}</p></div><button disabled={investigate.isPending} onClick={() => investigate.mutate(selectedRun.id)} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-cyan-700 bg-white px-4 text-sm font-semibold text-cyan-800 disabled:opacity-50">{investigate.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Bot className="size-4" />}启动只读调查</button></div>
              <div className="flex flex-col gap-3 border-t border-slate-200 pt-4 sm:flex-row"><div className="flex items-center gap-2 text-xs font-semibold text-slate-500"><Filter className="size-4" />筛选</div><select value={resourceFilter} onChange={(event) => setResourceFilter(event.target.value)} className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm"><option value="all">全部资源</option>{resourceTypes.map((item) => <option key={item} value={item}>{item}</option>)}</select><select value={differenceFilter} onChange={(event) => setDifferenceFilter(event.target.value)} className="h-9 rounded-lg border border-slate-300 bg-white px-3 text-sm"><option value="all">全部差异</option>{differenceTypes.map((item) => <option key={item} value={item}>{item.replaceAll("_", " ")}</option>)}</select><span className="self-center text-xs text-slate-500">{filtered.length} 条</span></div>
              {investigate.data && <div className="border-l-2 border-cyan-500 pl-3 text-sm text-cyan-800">调查 Run {investigate.data.run_id} · {investigate.data.status}</div>}{investigate.error && <div className="border-l-2 border-rose-500 pl-3 text-sm text-rose-700">{investigate.error.message}</div>}</div><DifferenceTable items={filtered} /></>}
          </div>
        </div>
      </section>
    </>}
  </div>;
}
