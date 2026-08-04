"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Database, RefreshCw, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { apiFetch } from "@/lib/api/client";

type Scenario = { scenario_id: string; seller_sku: string; available: number; reserved: number; inbound: number; daily_sales: number; price: string; title: string; data_source: string; verified_amazon_data: boolean };
type Connection = { id: string; status: string; marketplace_ids: string[] };
type HighFidelityResult = { dataset: { fingerprint: string; counts: Record<string, number> }; sync_status: string; customer_cases: number; verified_amazon_data: false };
type HighFidelitySubmission = { task_id: string; status: string; dataset_fingerprint: string; verified_amazon_data: false };
type HighFidelityTask = { task_id: string; status: string; result?: HighFidelityResult | null; error_code?: string | null; error_message?: string | null };

const labels: Record<string, string> = {
  healthy: "正常 SKU", critical_stockout: "触发库存骤降", out_of_stock: "触发断货", excess_inventory: "触发库存积压",
  contribution_loss: "触发利润亏损", missing_cost: "缺少成本", stale_inventory: "库存过期", listing_missing_fields: "Listing 缺字段",
  unknown_order_status: "未知订单状态", inventory_mismatch: "库存对账不一致",
};

export function MockScenarioConsole() {
  const client = useQueryClient();
  const scenarios = useQuery({ queryKey: ["mock-scenarios"], queryFn: () => apiFetch<{ items: Scenario[]; failures: Record<string, boolean> }>("/api/mock-scenarios"), refetchInterval: 5000 });
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => apiFetch<{ items: Connection[] }>("/api/connections") });
  const connectionId = useMemo(() => connections.data?.items.find(item => item.status === "active")?.id, [connections.data]);
  const trigger = useMutation({
    mutationFn: (scenarioId: string) => apiFetch(`/api/mock-scenarios/${scenarioId}/trigger`, { method: "POST", body: JSON.stringify({ connection_id: connectionId }) }),
    onSuccess: async () => { await client.invalidateQueries({ queryKey: ["mock-scenarios"] }); await client.invalidateQueries({ queryKey: ["issues"] }); },
  });
  const [notice, setNotice] = useState<string | null>(null);
  const [seedTaskId, setSeedTaskId] = useState<string | null>(null);
  const [highFidelityResult, setHighFidelityResult] = useState<HighFidelityResult | null>(null);
  const highFidelity = useMutation({
    mutationFn: () => apiFetch<HighFidelitySubmission>("/api/mock-scenarios/high-fidelity/seed", {
      method: "POST",
      body: JSON.stringify({
        connection_id: connectionId,
        seed: 20260722,
        days: 30,
        sku_count: 100,
        order_count: 10000,
        campaign_count: 50,
        search_term_count: 3000,
        return_count: 500,
        message_count: 800,
        review_count: 300,
        customer_case_count: 300,
        business_context_count: 50,
        reference_time: new Date().toISOString(),
      }),
    }),
    onSuccess: result => {
      setHighFidelityResult(null);
      setSeedTaskId(result.task_id);
      setNotice(`高拟真装载任务已提交：${result.task_id.slice(0, 12)}。可离开本页，任务会在后台继续。`);
    },
    onError: error => setNotice(error instanceof Error ? error.message : "高拟真数据载入失败"),
  });
  const seedTask = useQuery({
    queryKey: ["high-fidelity-seed-task", seedTaskId],
    queryFn: () => apiFetch<HighFidelityTask>(`/api/mock-scenarios/high-fidelity/tasks/${seedTaskId}`),
    enabled: Boolean(seedTaskId),
    refetchInterval: 3000,
  });
  useEffect(() => {
    const task = seedTask.data;
    if (!task || !seedTaskId || ["pending", "running"].includes(task.status)) return;
    if (task.status === "succeeded" && task.result) {
      setHighFidelityResult(task.result);
      setNotice(`高拟真数据已载入：${task.result.dataset.counts.skus} 个 SKU、${task.result.dataset.counts.orders} 个订单、${task.result.customer_cases} 个客服 Case。指纹 ${task.result.dataset.fingerprint.slice(0, 12)}。`);
      void client.invalidateQueries();
    } else {
      setNotice(`高拟真装载失败：${task.error_code ?? "unknown_error"}${task.error_message ? ` · ${task.error_message}` : ""}`);
    }
    setSeedTaskId(null);
  }, [client, seedTask.data, seedTaskId]);
  useEffect(() => {
    if (seedTask.error) setNotice(seedTask.error instanceof Error ? seedTask.error.message : "无法读取高拟真任务状态");
  }, [seedTask.error]);
  const reset = useMutation({
    mutationFn: () => apiFetch<{ deleted_tables?: string[] }>("/api/mock-scenarios/reset", { method: "POST" }),
    onSuccess: async (result) => {
      setNotice(`演示租户已重置，已清理 ${result.deleted_tables?.length ?? 0} 类派生数据。请重新同步后开始演示。`);
      await client.invalidateQueries();
    },
    onError: error => setNotice(error instanceof Error ? error.message : "演示租户重置失败"),
  });
  return <div className="space-y-6">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><p className="eyebrow">Amazon operations drill</p><h1 className="mt-2 text-3xl font-bold">Mock 场景控制台</h1><p className="mt-2 text-sm text-slate-600">所有数据来自 Mock Amazon，绝不代表已验证 Amazon 数据。</p></div><button onClick={() => reset.mutate()} disabled={reset.isPending} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-semibold"><RefreshCw className={`size-4 ${reset.isPending ? "animate-spin" : ""}`}/>重置演示租户</button></div>
    {notice && <div className="rounded-lg border border-cyan-200 bg-cyan-50 p-4 text-sm text-cyan-900">{notice}</div>}
    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"><AlertTriangle className="mr-2 inline size-4"/>Mock Seller Central 只改变演练数据；真实 Amazon 写闸门仍保持关闭。</div>
    {!connectionId && <div className="rounded-2xl bg-rose-50 p-4 text-sm text-rose-700">没有可用的 Mock Amazon 连接，请先完成本地授权。</div>}
    <section className="panel p-5"><div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between"><div><p className="text-xs font-bold uppercase text-cyan-700">30-day UAT dataset</p><h2 className="mt-1 text-lg font-bold">载入高拟真美国站店铺</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">100个SKU、10,000个订单、50个Sponsored Products Campaign、3,000个Search Term、500条退货、800条消息和300个客服Case。另含5类报表质量异常和4组动作归因试验，用于验证延迟、缺失、重算、币种隔离、幂等和收益声明边界，全程保持Mock/未验证。</p></div><button onClick={() => highFidelity.mutate()} disabled={!connectionId || highFidelity.isPending || Boolean(seedTaskId)} className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-cyan-700 px-4 text-sm font-bold text-white disabled:opacity-40"><Database className="size-4" />{highFidelity.isPending ? "正在提交任务" : seedTaskId ? "后台生成中" : "载入30天高拟真数据"}</button></div>{seedTaskId && <div className="mt-4 rounded-lg bg-cyan-50 p-3 text-sm text-cyan-900"><span className="font-semibold">后台任务 {seedTaskId.slice(0, 12)}</span><span className="ml-2">{seedTask.data?.status ?? "pending"}</span></div>}{highFidelityResult && <div className="mt-4 grid gap-3 sm:grid-cols-3"><div className="rounded-lg bg-slate-50 p-3 text-sm"><span className="text-slate-500">同步状态</span><strong className="mt-1 block">{highFidelityResult.sync_status}</strong></div><div className="rounded-lg bg-slate-50 p-3 text-sm"><span className="text-slate-500">客服Case</span><strong className="mt-1 block">{highFidelityResult.customer_cases}</strong></div><div className="rounded-lg bg-slate-50 p-3 text-sm"><span className="text-slate-500">Amazon验证</span><strong className="mt-1 block text-amber-700">否，合成数据</strong></div></div>}</section>
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{scenarios.data?.items.map(item => <section key={item.scenario_id} className="panel p-5"><div className="flex items-center justify-between"><Database className="size-5 text-brand-600"/><span className="rounded-full bg-slate-100 px-2 py-1 text-xs">{item.scenario_id}</span></div><h2 className="mt-4 font-bold">{labels[item.scenario_id] ?? item.scenario_id}</h2><p className="mt-1 text-sm text-slate-500">{item.seller_sku}</p><dl className="mt-4 grid grid-cols-3 gap-2 text-center text-xs"><div className="rounded-lg bg-slate-50 p-2"><dt>可售</dt><dd className="mt-1 font-bold">{item.available}</dd></div><div className="rounded-lg bg-slate-50 p-2"><dt>在途</dt><dd className="mt-1 font-bold">{item.inbound}</dd></div><div className="rounded-lg bg-slate-50 p-2"><dt>日销</dt><dd className="mt-1 font-bold">{item.daily_sales}</dd></div></dl><button onClick={() => trigger.mutate(item.scenario_id)} disabled={!connectionId || trigger.isPending} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40"><Zap className="size-4"/>触发并发送 Notification</button></section>)}</div>
    <div className="grid gap-3 md:grid-cols-2"><button disabled={!connectionId || trigger.isPending} onClick={() => trigger.mutate("amazon_429")} className="rounded-xl border border-amber-300 bg-amber-50 p-4 text-left text-sm font-semibold text-amber-900">触发一次 Amazon 429</button><button disabled={!connectionId || trigger.isPending} onClick={() => trigger.mutate("token_revoked")} className="rounded-xl border border-rose-300 bg-rose-50 p-4 text-left text-sm font-semibold text-rose-900">触发 Token 失效</button></div>
  </div>;
}
