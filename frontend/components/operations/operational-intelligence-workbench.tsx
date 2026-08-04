"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, BarChart3, Boxes, CheckCircle2, ChevronLeft, ChevronRight, CircleDollarSign, Database, Gauge, MousePointerClick, RefreshCw, Search, ShieldCheck, ShoppingCart, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { apiFetch } from "@/lib/api/client";

type Tab = "amazon" | "advertising" | "voc";
type Item = Record<string, any>;

function dateText(value: string | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-";
}

function metricText(value: unknown, kind: "rate" | "money" | "count" = "rate", currency = "USD") {
  if (value === null || value === undefined || value === "") return "数据不足";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  if (kind === "money") return new Intl.NumberFormat("zh-CN", { style: "currency", currency, maximumFractionDigits: 2 }).format(numeric);
  if (kind === "count") return new Intl.NumberFormat("zh-CN").format(numeric);
  return `${(numeric * 100).toFixed(1)}%`;
}

export function OperationalIntelligenceWorkbench() {
  const [tab, setTab] = useState<Tab>("amazon");
  const client = useQueryClient();
  const seed = useMutation({ mutationFn: () => apiFetch<Record<string, unknown>>("/api/mock-scenarios/operations-learning/seed", { method: "POST" }), onSuccess: async () => { await Promise.all([client.invalidateQueries({ queryKey: ["advertising-diagnostics"] }), client.invalidateQueries({ queryKey: ["advertising-snapshots"] }), client.invalidateQueries({ queryKey: ["advertising-search-terms"] }), client.invalidateQueries({ queryKey: ["voc-summaries"] }), client.invalidateQueries({ queryKey: ["work-items"] })]); } });
  const tabs: Array<{ key: Tab; label: string; icon: typeof Boxes }> = [
    { key: "amazon", label: "Amazon 核心异常", icon: Boxes },
    { key: "advertising", label: "广告诊断", icon: BarChart3 },
    { key: "voc", label: "退货 / VOC", icon: Search },
  ];
  return <div className="space-y-5">
    <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
      <div><p className="eyebrow">Amazon operations intelligence</p><h1 className="mt-2 text-3xl font-bold tracking-tight text-slate-950">异常与增长工作台</h1><p className="mt-2 text-sm text-slate-600">事实、诊断、负责人和验证结果统一进入运营任务。</p></div>
      <div className="flex flex-wrap items-center gap-2">{process.env.NEXT_PUBLIC_MOCK_DRILL_ENABLED === "true" && <button onClick={() => seed.mutate()} disabled={seed.isPending} className="inline-flex items-center gap-2 rounded-lg border border-cyan-300 bg-cyan-50 px-3 py-2 text-xs font-bold text-cyan-800 disabled:opacity-40"><Database className="size-4" />{seed.isPending ? "正在载入" : "载入闭环测试数据"}</button>}<div className="inline-flex w-fit rounded-lg bg-slate-200 p-1">{tabs.map(({ key, label, icon: Icon }) => <button key={key} onClick={() => setTab(key)} className={"inline-flex items-center gap-2 rounded-md px-3 py-2 text-xs font-semibold " + (tab === key ? "bg-white text-slate-950 shadow-sm" : "text-slate-600")}><Icon className="size-4" />{label}</button>)}</div></div>
    </header>
    {seed.isSuccess && <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-800">测试批次已进入广告、VOC和运营任务闭环。</div>}
    {seed.error && <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{seed.error.message}</div>}
    {tab === "amazon" ? <AmazonSignals /> : tab === "advertising" ? <AdvertisingDiagnostics /> : <VocSummaries />}
  </div>;
}

function AmazonSignals() {
  const query = useQuery({ queryKey: ["amazon-signals"], queryFn: () => apiFetch<{ items: Item[] }>("/api/amazon/signals"), refetchInterval: 15000 });
  const items = query.data?.items ?? [];
  return <section className="panel overflow-hidden">
    <SectionHead title="Amazon 核心异常" icon={<ShieldCheck className="size-4" />} onRefresh={() => query.refetch()} />
    <div className="overflow-x-auto"><table className="w-full min-w-[980px] text-left text-sm"><thead className="border-b border-slate-200 text-xs text-slate-500"><tr><th className="px-4 py-3">异常</th><th className="px-4 py-3">资源</th><th className="px-4 py-3">严重度</th><th className="px-4 py-3">Amazon 证据</th><th className="px-4 py-3">新鲜度</th><th className="px-4 py-3">任务</th></tr></thead><tbody>{items.map((item) => <tr key={String(item.id)} className="border-b border-slate-100 last:border-0"><td className="px-4 py-4"><p className="font-semibold">{String(item.title)}</p><p className="mt-1 text-xs text-slate-500">{String(item.signal_type)} · {String(item.marketplace_id)}</p></td><td className="px-4 py-4 text-xs text-slate-700">{String(item.resource_id)}<p className="mt-1 text-slate-400">观测 {dateText(String(item.observed_at))}</p></td><td className="px-4 py-4"><span className={"rounded-full px-2 py-1 text-xs font-bold " + (item.severity === "critical" ? "bg-rose-100 text-rose-700" : "bg-amber-100 text-amber-700")}>{String(item.severity)}</span></td><td className="px-4 py-4 text-xs">{item.verified_amazon_data === true ? <span className="inline-flex items-center gap-1 font-semibold text-emerald-700"><CheckCircle2 className="size-4" />已验证</span> : <span className="text-amber-700">未验证：{String(item.source)}</span>}</td><td className="px-4 py-4 text-xs text-slate-600">{String(item.freshness_seconds)} 秒<p className="mt-1 text-slate-400">采集 {dateText(String(item.collected_at))}</p></td><td className="px-4 py-4 text-xs font-semibold text-cyan-700">{String(item.work_item_id ?? "-")}</td></tr>)}</tbody></table>{!query.isLoading && items.length === 0 && <Empty text="当前没有 Amazon 异常" />}</div>
  </section>;
}

function AdvertisingDiagnostics() {
  const client = useQueryClient();
  const [diagnosticPage, setDiagnosticPage] = useState(1);
  const diagnosticPageSize = 50;
  const [searchTermPage, setSearchTermPage] = useState(1);
  const searchTermPageSize = 50;
  const diagnostics = useQuery({
    queryKey: ["advertising-diagnostics", diagnosticPage, diagnosticPageSize],
    queryFn: () => apiFetch<{ items: Item[]; total: number; page: number; pages: number; has_next: boolean }>(`/api/advertising/diagnostics?page=${diagnosticPage}&page_size=${diagnosticPageSize}`),
    refetchInterval: 15000,
  });
  const snapshots = useQuery({ queryKey: ["advertising-snapshots"], queryFn: () => apiFetch<{ items: Item[] }>("/api/advertising/snapshots"), refetchInterval: 15000 });
  const searchTerms = useQuery({
    queryKey: ["advertising-search-terms", searchTermPage, searchTermPageSize],
    queryFn: () => apiFetch<{ items: Item[]; total: number; page: number; pages: number; has_next: boolean }>(`/api/advertising/search-terms?page=${searchTermPage}&page_size=${searchTermPageSize}`),
    refetchInterval: 15000,
  });
  const [form, setForm] = useState({ seller_sku: "", campaign_id: "", spend: "", attributed_sales: "", total_sales: "", budget: "", impressions: "", clicks: "", orders: "" });
  const [termForm, setTermForm] = useState({ seller_sku: "", campaign_id: "", search_term: "", spend: "", attributed_sales: "", impressions: "", clicks: "", orders: "" });
  const save = useMutation({ mutationFn: () => apiFetch("/api/advertising/snapshots", { method: "POST", body: JSON.stringify({ marketplace_id: "ATVPDKIKX0DER", seller_sku: form.seller_sku, entity_type: "campaign", entity_id: form.campaign_id, campaign_id: form.campaign_id, period_start: new Date(Date.now() - 86400000).toISOString(), period_end: new Date().toISOString(), currency: "USD", spend: form.spend, attributed_sales: form.attributed_sales || null, total_sales: form.total_sales || null, budget: form.budget || null, impressions: form.impressions ? Number(form.impressions) : null, clicks: form.clicks ? Number(form.clicks) : null, orders: form.orders ? Number(form.orders) : null, source: "manual", verified_amazon_data: false, elapsed_days: "1", period_days: "7" }) }), onSuccess: async () => { setDiagnosticPage(1); await Promise.all([snapshots.refetch(), client.invalidateQueries({ queryKey: ["advertising-diagnostics"] }), client.invalidateQueries({ queryKey: ["work-items"] })]); } });
  const saveTerm = useMutation({ mutationFn: () => apiFetch("/api/advertising/search-terms", { method: "POST", body: JSON.stringify({ marketplace_id: "ATVPDKIKX0DER", ...termForm, period_start: new Date(Date.now() - 86400000).toISOString(), period_end: new Date().toISOString(), currency: "USD", attributed_sales: termForm.attributed_sales || null, impressions: termForm.impressions ? Number(termForm.impressions) : null, clicks: termForm.clicks ? Number(termForm.clicks) : null, orders: termForm.orders ? Number(termForm.orders) : null, source: "manual", verified_amazon_data: false }) }), onSuccess: async () => { setSearchTermPage(1); await client.invalidateQueries({ queryKey: ["advertising-search-terms"] }); setTermForm({ seller_sku: "", campaign_id: "", search_term: "", spend: "", attributed_sales: "", impressions: "", clicks: "", orders: "" }); } });
  const fields: Array<[keyof typeof form, string]> = [["seller_sku", "SKU"], ["campaign_id", "Campaign"], ["spend", "花费"], ["attributed_sales", "广告销售额"], ["total_sales", "总销售额"], ["budget", "预算"], ["impressions", "曝光"], ["clicks", "点击"], ["orders", "订单"]];
  const latest = snapshots.data?.items[0];
  const metrics = latest?.metrics ?? {};
  const cards = [
    ["ACoS", metricText(metrics.acos), CircleDollarSign], ["TACOS", metricText(metrics.tacos), Gauge],
    ["CTR", metricText(metrics.ctr), MousePointerClick], ["CVR", metricText(metrics.cvr), ShoppingCart],
    ["CPC", metricText(metrics.cpc, "money", String(latest?.currency ?? "USD")), BarChart3],
    ["预算消耗", latest?.budget ? metricText(Number(latest.spend) / Number(latest.budget)) : "数据不足", Database],
  ] as const;
  return <div className="space-y-5">
    <section>
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between"><div><p className="eyebrow">Latest campaign snapshot</p><h2 className="mt-1 font-semibold text-slate-950">核心广告指标</h2></div>{latest && <div className="text-xs text-slate-500"><span className={latest.verified_amazon_data ? "font-semibold text-emerald-700" : "font-semibold text-amber-700"}>{latest.verified_amazon_data ? "Amazon Ads 已验证" : `未验证：${String(latest.source)}`}</span><span className="ml-2">观测 {dateText(String(latest.observed_at))}</span></div>}</div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">{cards.map(([label, value, Icon]) => <div key={label} className="panel flex min-h-28 items-center justify-between p-4"><div><p className="text-xs font-medium text-slate-500">{label}</p><p className="mt-2 text-xl font-bold text-slate-950">{value}</p></div><span className="grid size-9 place-items-center rounded-lg bg-slate-100 text-slate-600"><Icon className="size-4" /></span></div>)}</div>
      {!snapshots.isLoading && !latest && <div className="panel mt-3 p-4 text-sm text-slate-500">录入广告日报后，这里会显示 ACoS、TACOS、CTR、CVR、CPC 和预算消耗。</div>}
    </section>
    <section className="panel p-4"><div className="flex items-center gap-2"><Database className="size-4 text-cyan-700" /><h2 className="font-semibold">广告日报快照</h2></div><div className="mt-4 grid gap-3 sm:grid-cols-3 lg:grid-cols-5">{fields.map(([key, label]) => <label key={key} className="text-xs font-semibold text-slate-600">{label}<input value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm font-normal" /></label>)}<button onClick={() => save.mutate()} disabled={save.isPending || !form.seller_sku || !form.campaign_id || !form.spend} className="self-end rounded-lg bg-cyan-700 px-3 py-2 text-xs font-bold text-white disabled:opacity-40">计算诊断</button></div>{save.error && <p className="mt-3 text-xs text-rose-700">{save.error.message}</p>}</section>
    <section className="panel overflow-hidden"><SectionHead title="预算与效率异常" icon={<TriangleAlert className="size-4 text-amber-600" />} onRefresh={() => diagnostics.refetch()} /><DiagnosticTable items={diagnostics.data?.items ?? []} loading={diagnostics.isLoading} /><DiagnosticPagination total={diagnostics.data?.total ?? 0} page={diagnostics.data?.page ?? diagnosticPage} pages={diagnostics.data?.pages ?? 0} loading={diagnostics.isFetching} onPageChange={setDiagnosticPage} /></section>
    <section className="panel overflow-hidden"><SectionHead title="Search Term 表现" icon={<BarChart3 className="size-4" />} onRefresh={() => searchTerms.refetch()} /><div className="grid gap-3 border-b border-slate-200 bg-slate-50 p-4 sm:grid-cols-2 lg:grid-cols-4">{([ ["seller_sku", "SKU"], ["campaign_id", "Campaign"], ["search_term", "Search Term"], ["spend", "花费"], ["attributed_sales", "广告销售额"], ["impressions", "曝光"], ["clicks", "点击"], ["orders", "订单"] ] as Array<[keyof typeof termForm, string]>).map(([key, label]) => <label key={key} className="text-xs font-semibold text-slate-600">{label}<input value={termForm[key]} onChange={(event) => setTermForm({ ...termForm, [key]: event.target.value })} className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-normal" /></label>)}<button onClick={() => saveTerm.mutate()} disabled={saveTerm.isPending || !termForm.seller_sku || !termForm.campaign_id || !termForm.search_term || !termForm.spend} className="rounded-lg bg-slate-950 px-3 py-2 text-xs font-bold text-white disabled:opacity-40 lg:col-start-4">保存 Search Term</button></div>{saveTerm.error && <p className="border-b border-slate-200 px-4 py-3 text-xs text-rose-700">{saveTerm.error.message}</p>}<div className="overflow-x-auto"><table className="w-full min-w-[980px] text-left text-sm"><thead className="border-b border-slate-200 text-xs text-slate-500"><tr><th className="px-4 py-3">Search Term</th><th className="px-4 py-3">SKU / Campaign</th><th className="px-4 py-3">花费 / 销售额</th><th className="px-4 py-3">ACoS</th><th className="px-4 py-3">CTR / CVR</th><th className="px-4 py-3">来源</th></tr></thead><tbody>{(searchTerms.data?.items ?? []).map((item) => <tr key={String(item.id)} className="border-b border-slate-100 last:border-0"><td className="px-4 py-4 font-medium">{String(item.search_term)}</td><td className="px-4 py-4 text-xs">{String(item.seller_sku)}<p className="mt-1 text-slate-400">{String(item.campaign_id)}</p></td><td className="px-4 py-4">{metricText(item.spend, "money", String(item.currency))}<p className="mt-1 text-xs text-slate-500">销售 {metricText(item.attributed_sales, "money", String(item.currency))}</p></td><td className="px-4 py-4 font-semibold">{metricText(item.metrics?.acos)}</td><td className="px-4 py-4 text-xs">{metricText(item.metrics?.ctr)} / {metricText(item.metrics?.cvr)}</td><td className="px-4 py-4 text-xs"><span className={item.verified_amazon_data ? "font-semibold text-emerald-700" : "font-semibold text-amber-700"}>{item.verified_amazon_data ? "Amazon Ads 已验证" : String(item.source)}</span><p className="mt-1 text-slate-400">{dateText(String(item.observed_at))}</p></td></tr>)}</tbody></table>{!searchTerms.isLoading && !(searchTerms.data?.items.length) && <Empty text="暂无 Search Term 快照" />}</div></section>
    <SearchTermPagination
      total={searchTerms.data?.total ?? 0}
      page={searchTerms.data?.page ?? searchTermPage}
      pages={searchTerms.data?.pages ?? 0}
      loading={searchTerms.isFetching}
      onPageChange={setSearchTermPage}
    />
  </div>;
}

function SearchTermPagination({ total, page, pages, loading, onPageChange }: { total: number; page: number; pages: number; loading: boolean; onPageChange: (page: number) => void }) {
  return <div className="panel flex min-h-14 flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
    <p className="text-slate-600">共 <span className="font-semibold text-slate-950">{new Intl.NumberFormat("zh-CN").format(total)}</span> 条 Search Term，每页 50 条</p>
    <div className="flex items-center gap-2">
      <button type="button" aria-label="上一页 Search Term" title="上一页" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={loading || page <= 1} className="grid size-9 place-items-center rounded-lg border border-slate-300 text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"><ChevronLeft className="size-4" /></button>
      <span className="min-w-24 text-center text-xs font-semibold text-slate-700">第 {page} / {Math.max(pages, 1)} 页</span>
      <button type="button" aria-label="下一页 Search Term" title="下一页" onClick={() => onPageChange(Math.min(pages, page + 1))} disabled={loading || pages === 0 || page >= pages} className="grid size-9 place-items-center rounded-lg border border-slate-300 text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"><ChevronRight className="size-4" /></button>
    </div>
  </div>;
}

function DiagnosticPagination({ total, page, pages, loading, onPageChange }: { total: number; page: number; pages: number; loading: boolean; onPageChange: (page: number) => void }) {
  return <div className="flex min-h-14 flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 text-sm">
    <p className="text-slate-600">共 <span className="font-semibold text-slate-950">{new Intl.NumberFormat("zh-CN").format(total)}</span> 条广告诊断，每页 50 条</p>
    <div className="flex items-center gap-2">
      <button type="button" aria-label="上一页广告诊断" title="上一页" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={loading || page <= 1} className="grid size-9 place-items-center rounded-lg border border-slate-300 text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"><ChevronLeft className="size-4" /></button>
      <span className="min-w-24 text-center text-xs font-semibold text-slate-700">第 {page} / {Math.max(pages, 1)} 页</span>
      <button type="button" aria-label="下一页广告诊断" title="下一页" onClick={() => onPageChange(Math.min(pages, page + 1))} disabled={loading || pages === 0 || page >= pages} className="grid size-9 place-items-center rounded-lg border border-slate-300 text-slate-700 disabled:cursor-not-allowed disabled:opacity-40"><ChevronRight className="size-4" /></button>
    </div>
  </div>;
}

function DiagnosticTable({ items, loading }: { items: Item[]; loading: boolean }) {
  return <div className="overflow-x-auto"><table className="w-full min-w-[900px] text-left text-sm"><thead className="border-b border-slate-200 text-xs text-slate-500"><tr><th className="px-4 py-3">诊断</th><th className="px-4 py-3">SKU / Campaign</th><th className="px-4 py-3">等级</th><th className="px-4 py-3">证据指标</th><th className="px-4 py-3">工作项</th></tr></thead><tbody>{items.map((item) => <tr key={String(item.id)} className="border-b border-slate-100 last:border-0"><td className="px-4 py-4 font-semibold">{String(item.title)}<p className="mt-1 text-xs text-slate-500">{String(item.code)}</p></td><td className="px-4 py-4 text-xs">{String(item.seller_sku)}<p className="mt-1 text-slate-400">{String(item.campaign_id)}</p></td><td className="px-4 py-4"><span className="rounded-full bg-amber-100 px-2 py-1 text-xs font-bold text-amber-700">{String(item.severity)}</span></td><td className="max-w-72 px-4 py-4 text-xs text-slate-600"><code>{JSON.stringify(item.details)}</code></td><td className="px-4 py-4 text-xs font-semibold text-cyan-700">{String(item.work_item_id ?? "-")}</td></tr>)}</tbody></table>{!loading && items.length === 0 && <Empty text="当前没有广告诊断" />}</div>;
}

function VocSummaries() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["voc-summaries"], queryFn: () => apiFetch<{ items: Item[] }>("/api/voc/summaries"), refetchInterval: 15000 });
  const [sellerSku, setSellerSku] = useState("");
  const summary = useMutation({ mutationFn: () => apiFetch("/api/voc/summaries", { method: "POST", body: JSON.stringify({ seller_sku: sellerSku, marketplace_id: "ATVPDKIKX0DER", period_start: new Date(Date.now() - 30 * 86400000).toISOString(), period_end: new Date().toISOString(), min_sample_size: 5 }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["voc-summaries"] }) });
  const items = query.data?.items ?? [];
  return <div className="space-y-5">
    <section className="panel p-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs font-semibold text-slate-600">按 SKU 汇总退货、评论和客服标签<input value={sellerSku} onChange={(event) => setSellerSku(event.target.value)} placeholder="Seller SKU" className="mt-1 block w-64 rounded-lg border border-slate-300 px-3 py-2 text-sm font-normal" /></label>
        <button onClick={() => summary.mutate()} disabled={!sellerSku.trim() || summary.isPending} className="rounded-lg bg-slate-950 px-3 py-2 text-xs font-bold text-white disabled:opacity-40">生成 SKU 汇总</button>
      </div>
      {summary.error && <p className="mt-3 text-xs text-rose-700">{summary.error.message}</p>}
    </section>
    <section className="panel overflow-hidden">
      <SectionHead title="SKU VOC 改进队列" icon={<AlertOctagon className="size-4 text-rose-600" />} onRefresh={() => query.refetch()} />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1180px] text-left text-sm">
          <thead className="border-b border-slate-200 text-xs text-slate-500"><tr><th className="px-4 py-3">SKU</th><th className="px-4 py-3">样本</th><th className="px-4 py-3">Top 原因</th><th className="px-4 py-3">来源构成</th><th className="px-4 py-3">结论</th><th className="px-4 py-3">负责团队</th><th className="px-4 py-3">证据来源</th><th className="px-4 py-3">工作项</th></tr></thead>
          <tbody>{items.map((item) => <tr key={String(item.id)} className="border-b border-slate-100 last:border-0">
            <td className="px-4 py-4 font-semibold">{String(item.seller_sku)}<p className="mt-1 text-xs text-slate-500">{dateText(String(item.period_start))} - {dateText(String(item.period_end))}</p></td>
            <td className="px-4 py-4">{String(item.sample_size)}</td>
            <td className="px-4 py-4"><span className="font-semibold">{String(item.top_reason_code ?? "-")}</span><p className="mt-1 text-xs text-slate-500">{String(item.top_reason_count)} 条</p></td>
            <td className="px-4 py-4 text-xs text-slate-600">退货 {String(item.return_count)} · 评论 {String(item.review_count)} · 客服 {String(item.message_count)}</td>
            <td className="px-4 py-4">{item.evidence_status === "sufficient" ? <span className="inline-flex items-center gap-1 font-bold text-rose-700"><AlertOctagon className="size-4" />需改进</span> : <span className="font-semibold text-amber-700">证据不足</span>}</td>
            <td className="px-4 py-4 text-xs font-semibold text-slate-700">{vocTeamText(item.route?.team)}<p className="mt-1 font-normal text-slate-400">{String(item.route?.domain ?? "operations")}</p></td>
            <td className="px-4 py-4 text-xs"><span className="font-semibold text-amber-700">未验证聚合</span><p className="mt-1 text-slate-400">{String(item.source ?? "deterministic_voc_aggregation")}</p></td>
            <td className="px-4 py-4 text-xs font-semibold text-cyan-700">{String(item.work_item_id ?? "-")}</td>
          </tr>)}</tbody>
        </table>
        {!query.isLoading && items.length === 0 && <Empty text="暂无 VOC 汇总" />}
      </div>
    </section>
  </div>;
}

function vocTeamText(value: unknown) {
  const labels: Record<string, string> = { listing: "Listing 团队", supply_chain: "供应链 / 包装", quality: "质量 / 供应商", customer_service: "内容与客服", operations: "仓储 / 运营" };
  return labels[String(value ?? "operations")] ?? String(value ?? "operations");
}

function SectionHead({ title, icon, onRefresh }: { title: string; icon: React.ReactNode; onRefresh: () => void }) {
  return <div className="flex items-center justify-between border-b border-slate-200 p-4"><div className="flex items-center gap-2"><span className="text-slate-700">{icon}</span><h2 className="font-semibold">{title}</h2></div><button onClick={() => onRefresh()} className="grid size-8 place-items-center rounded-lg border border-slate-300" aria-label={"刷新" + title}><RefreshCw className="size-4" /></button></div>;
}

function Empty({ text }: { text: string }) {
  return <div className="grid min-h-32 place-items-center text-sm text-slate-500">{text}</div>;
}
