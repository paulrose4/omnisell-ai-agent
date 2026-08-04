"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, CircleDollarSign, Clock3, DatabaseZap, Headphones, Link2Off, LoaderCircle, RefreshCw, ShieldAlert, ShieldCheck, UserRound } from "lucide-react";
import Link from "next/link";
import { apiFetch, ApiClientError } from "@/lib/api/client";
import type { IssueSeverity, OperationsDashboardResponse } from "@/lib/api/types";
import { StatusBadge } from "@/components/ui/status-badge";

const severityOrder: IssueSeverity[] = ["critical", "high", "medium", "low"];

function MetricCard({ label, value, icon: Icon, tone, href }: { label: string; value: string | number; icon: typeof AlertTriangle; tone: string; href: string }) {
  return <Link href={href} className="panel block p-4 transition hover:border-slate-400 hover:bg-slate-50"><div className="flex items-start justify-between"><div><p className="text-xs font-medium text-slate-500">{label}</p><p className="mt-2 text-2xl font-bold tracking-tight text-slate-950">{value}</p></div><div className={`grid size-10 place-items-center rounded-lg ${tone}`}><Icon className="size-5" /></div></div></Link>;
}

function formatImpact(value: string, currency: string) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency, maximumFractionDigits: 0 }).format(Number(value));
}

export function OperationsDashboard() {
  const queryClient = useQueryClient();
  const dashboard = useQuery({
    queryKey: ["operations-dashboard"],
    queryFn: () => apiFetch<OperationsDashboardResponse>("/api/operations/dashboard"),
    retry: (count, error) => !(error instanceof ApiClientError && error.status === 404) && count < 2,
  });
  const generate = useMutation({
    mutationFn: () => apiFetch("/api/operations/briefs/generate", {
      method: "POST",
      body: JSON.stringify({ timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", scan_listings: true }),
    }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["operations-dashboard"] }),
  });

  if (dashboard.isLoading) return <div className="grid min-h-80 place-items-center"><LoaderCircle className="size-8 animate-spin text-brand-500" /></div>;
  if (dashboard.error instanceof ApiClientError && dashboard.error.status === 404) {
    return <div className="panel grid min-h-80 place-items-center p-8 text-center"><div><DatabaseZap className="mx-auto size-12 text-slate-300" /><h2 className="mt-4 text-lg font-semibold">尚未生成运营简报</h2><p className="mt-2 text-sm text-slate-500">基于当前利润、库存、Listing 和数据质量异常生成今日优先事项。</p><button onClick={() => generate.mutate()} disabled={generate.isPending} className="mt-5 inline-flex items-center gap-2 rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60">{generate.isPending && <LoaderCircle className="size-4 animate-spin" />}生成今日简报</button></div></div>;
  }
  if (dashboard.error || !dashboard.data) return <div className="panel p-6 text-sm text-rose-700">经营数据加载失败：{dashboard.error?.message}</div>;

  const { brief, priorities, queue_summary: queueSummary, sku_health: skuHealth } = dashboard.data;
  const firstCurrency = Object.entries(brief.impact_by_currency)[0];
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: brief.timezone }).format(new Date());
  const briefIsStale = brief.brief_date !== today;
  return <div className="space-y-6">
    {dashboard.data.data_mode === "mock" && <div className="rounded-2xl border border-amber-300 bg-amber-50 px-5 py-4 text-sm text-amber-900"><strong>Golden Mock 演示数据：</strong>这些订单、成本、库存和异常均为合成数据，不代表已验证的 Amazon 数据。</div>}
    {briefIsStale && <div className="rounded-2xl border border-orange-200 bg-orange-50 px-5 py-4 text-sm text-orange-800">当前简报日期为 {brief.brief_date}，今天为 {today}。请重新生成简报后再做运营判断。</div>}
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
      <MetricCard label="紧急任务" value={queueSummary.urgent} icon={ShieldAlert} tone="bg-rose-50 text-rose-600" href="/work-items?view=active" />
      <MetricCard label="已逾期" value={queueSummary.overdue} icon={Clock3} tone="bg-orange-50 text-orange-600" href="/work-items?view=active" />
      <MetricCard label="待审批" value={queueSummary.awaiting_approval} icon={ShieldCheck} tone="bg-amber-50 text-amber-700" href="/work-items?view=awaiting_approval" />
      <MetricCard label="数据过期" value={queueSummary.stale_data} icon={DatabaseZap} tone="bg-yellow-50 text-yellow-700" href="/issues" />
      <MetricCard label="验证失败" value={queueSummary.verification_failed} icon={AlertTriangle} tone="bg-violet-50 text-violet-700" href="/work-items?view=verification_failed" />
      <MetricCard label="我的班次" value={queueSummary.my_tasks} icon={UserRound} tone="bg-cyan-50 text-cyan-700" href="/work-items?view=mine" />
    </div>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="待处理客服 Case" value={queueSummary.open_customer_cases} icon={Headphones} tone="bg-blue-50 text-blue-700" href="/customer-cases" />
      <MetricCard label="客诉根因整改" value={queueSummary.actionable_case_clusters} icon={ShieldAlert} tone="bg-rose-50 text-rose-700" href="/work-items?view=active" />
      <MetricCard label="未生成整改任务" value={queueSummary.unlinked_actionable_case_clusters} icon={Link2Off} tone="bg-amber-50 text-amber-700" href="/work-items?view=active" />
      <MetricCard label="信息流异常" value={queueSummary.case_flow_inconsistencies} icon={AlertTriangle} tone="bg-violet-50 text-violet-700" href="/work-items?view=active" />
    </div>

    <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
      <section className="panel overflow-hidden">
        <div className="flex items-center justify-between border-b border-slate-200 p-5 lg:px-6"><div><p className="eyebrow">Today priorities</p><h2 className="mt-1 font-semibold text-slate-950">今日优先事项</h2></div><button onClick={() => generate.mutate()} className="grid size-9 place-items-center rounded-xl border border-slate-300 text-slate-600 hover:bg-slate-50" aria-label="重新生成"><RefreshCw className={`size-4 ${generate.isPending ? "animate-spin" : ""}`} /></button></div>
        <div className="divide-y divide-slate-100">
          {priorities.slice(0, 5).map(({ issue, action }, index) => <div key={issue.id} className="p-5 lg:px-6"><div className="flex gap-4"><div className="grid size-8 shrink-0 place-items-center rounded-full bg-slate-100 text-xs font-bold text-slate-500">{index + 1}</div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><StatusBadge value={issue.severity} /><span className="text-xs font-medium text-slate-500">{issue.seller_sku}</span></div><Link href={`/issues?issue=${issue.id}`} className="mt-2 inline-flex items-center gap-1 font-semibold text-slate-950 hover:text-cyan-700">{issue.title}<ArrowRight className="size-4" /></Link><p className="mt-1 text-sm leading-6 text-slate-600">{issue.summary}</p>{action && <div className="mt-3 rounded-lg bg-brand-50 p-3"><p className="text-xs font-semibold text-brand-700">建议动作</p><p className="mt-1 text-sm text-brand-900">{action.title}</p></div>}</div></div></div>)}
          {!priorities.length && <div className="p-10 text-center text-sm text-slate-500">当前没有待处理异常。</div>}
        </div>
      </section>

      <div className="space-y-6">
        <section className="panel p-5 lg:p-6"><div className="flex items-center gap-3"><div className="grid size-10 place-items-center rounded-xl bg-emerald-50 text-emerald-600"><CircleDollarSign className="size-5" /></div><div><p className="text-sm font-semibold">量化业务影响</p><p className="text-xs text-slate-500">按币种隔离，不进行错误汇率合并</p></div></div>{firstCurrency ? <div className="mt-5 space-y-3">{Object.entries(brief.impact_by_currency).map(([currency, impact]) => <div key={currency} className="rounded-xl border border-slate-200 p-4"><p className="text-xs font-bold text-slate-500">{currency}</p><div className="mt-3 grid grid-cols-3 gap-2 text-sm"><div><p className="text-xs text-slate-500">销售额</p><p className="mt-1 font-semibold">{formatImpact(impact.revenue, currency)}</p></div><div><p className="text-xs text-slate-500">利润</p><p className="mt-1 font-semibold">{formatImpact(impact.profit, currency)}</p></div><div><p className="text-xs text-slate-500">成本</p><p className="mt-1 font-semibold">{formatImpact(impact.cost, currency)}</p></div></div></div>)}</div> : <p className="mt-5 text-sm text-slate-500">暂无带币种的量化影响。</p>}</section>
        <section className="panel p-5 lg:p-6"><div className="flex items-center justify-between"><div><p className="eyebrow">SKU health</p><h2 className="mt-1 font-semibold">SKU 风险概览</h2></div><Link href="/sku-health" className="inline-flex items-center gap-1 text-sm font-semibold text-brand-600">查看全部<ArrowRight className="size-4" /></Link></div><div className="mt-4 space-y-3">{skuHealth.slice(0, 5).map((sku) => <div key={`${sku.marketplace_id}-${sku.seller_sku}`} className="flex items-center justify-between rounded-xl border border-slate-200 p-3"><div><p className="text-sm font-semibold">{sku.seller_sku}</p><p className="mt-1 text-xs text-slate-500">{sku.open_issue_count} 个异常 · {sku.marketplace_id}</p></div><StatusBadge value={sku.highest_severity} /></div>)}</div></section>
      </div>
    </div>

    <p className="text-xs text-slate-400">简报日期 {brief.brief_date} · {brief.timezone} · 未分配币种异常 {brief.unallocated_impact_issue_count} 个</p>
  </div>;
}
