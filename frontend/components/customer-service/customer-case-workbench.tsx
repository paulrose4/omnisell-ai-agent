"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Clock3, MessageSquareText, ShieldAlert } from "lucide-react";
import { apiFetch } from "@/lib/api/client";
import type { CustomerCase, CustomerCaseListResponse } from "@/lib/api/types";

export function CustomerCaseWorkbench() {
  const client = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [rootCause, setRootCause] = useState("unknown");
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const cases = useQuery({ queryKey: ["customer-cases", page], queryFn: () => apiFetch<CustomerCaseListResponse>(`/api/customer-cases?page=${page}&page_size=${pageSize}`), refetchInterval: 5000 });
  useEffect(() => {
    const deepLinkedCaseId = new URLSearchParams(window.location.search).get("case");
    if (deepLinkedCaseId) setSelectedId(deepLinkedCaseId);
  }, []);
  useEffect(() => {
    const hasDeepLink = new URLSearchParams(window.location.search).has("case");
    if (!hasDeepLink && !selectedId && cases.data?.items[0]) setSelectedId(cases.data.items[0].id);
  }, [cases.data, selectedId]);
  useEffect(() => { if (cases.data && cases.data.pages > 0 && page > cases.data.pages) { setPage(cases.data.pages); setSelectedId(null); } }, [cases.data, page]);
  const detail = useQuery({ queryKey: ["customer-case", selectedId], queryFn: () => apiFetch<CustomerCase>(`/api/customer-cases/${selectedId}`), enabled: Boolean(selectedId), refetchInterval: 5000 });
  useEffect(() => { setReply(detail.data?.approved_reply ?? detail.data?.draft_reply ?? ""); }, [detail.data?.id, detail.data?.approved_reply, detail.data?.draft_reply]);
  useEffect(() => { setRootCause(detail.data?.root_cause_code ?? "unknown"); }, [detail.data?.id, detail.data?.root_cause_code]);

  const refresh = async () => { await client.invalidateQueries({ queryKey: ["customer-cases"] }); await client.invalidateQueries({ queryKey: ["customer-case", selectedId] }); };
  const transition = useMutation({ mutationFn: (status: string) => apiFetch(`/api/customer-cases/${selectedId}`, { method: "PATCH", body: JSON.stringify({ status, note: "Updated from Case 360" }) }), onSuccess: refresh });
  const send = useMutation({ mutationFn: () => apiFetch(`/api/customer-cases/${selectedId}/send`, { method: "POST", body: JSON.stringify({ reply, note: "Sent through Mock Buyer-Seller Messaging" }) }), onSuccess: refresh });
  const resolve = useMutation({ mutationFn: () => apiFetch(`/api/customer-cases/${selectedId}/resolve`, { method: "POST", body: JSON.stringify({ resolution_code: "information_provided", root_cause_code: rootCause, note: "Closed from Case 360 with confirmed root cause" }) }), onSuccess: refresh });
  const current = detail.data;

  function changePage(nextPage: number) {
    setSelectedId(null);
    setPage(nextPage);
  }

  return <div className="space-y-6">
    <div><p className="eyebrow">Customer service control tower</p><h1 className="mt-2 text-3xl font-bold">客服 Case 360</h1><p className="mt-2 text-sm text-slate-600">统一查看买家消息、订单事实、SOP、Policy、审批和处理时间线。Mock 数据不会被展示为真实 Amazon 数据。</p></div>
    <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
      <aside className="panel overflow-hidden">
        <div className="border-b border-slate-200 p-4"><p className="font-semibold">客服队列</p><p className="text-xs text-slate-500">{cases.data?.total ?? 0} 个 Case</p></div>
        <div className="max-h-[700px] divide-y divide-slate-100 overflow-y-auto">{cases.data?.items.map(item => <button key={item.id} onClick={() => setSelectedId(item.id)} className={`w-full p-4 text-left transition ${selectedId === item.id ? "bg-brand-50" : "hover:bg-slate-50"}`}>
          <div className="flex items-center justify-between gap-2"><span className={`rounded-full px-2 py-1 text-[11px] font-bold ${item.severity === "critical" || item.severity === "high" ? "bg-rose-50 text-rose-700" : "bg-slate-100 text-slate-600"}`}>{item.severity}</span><span className="text-[11px] text-slate-500">{item.status}</span></div>
          <p className="mt-2 font-semibold">{item.intent}</p><p className="mt-1 line-clamp-2 text-sm text-slate-600">{item.customer_message}</p><p className="mt-2 text-xs text-slate-400">{item.external_order_id ?? "无订单"} · 优先级 {item.priority_score}</p>
        </button>)}</div><CasePagination page={page} pages={cases.data?.pages ?? 0} total={cases.data?.total ?? 0} loading={cases.isFetching} onPageChange={changePage} />
      </aside>
      {!current ? <section className="panel grid min-h-96 place-items-center text-sm text-slate-500">选择一个客服 Case</section> : <section className="space-y-5">
        <div className="panel p-5">
          <div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex flex-wrap gap-2"><span className="rounded-full bg-slate-950 px-2.5 py-1 text-xs font-bold text-white">{current.status}</span><span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-bold text-amber-700">{current.intent}</span><span className="rounded-full bg-blue-50 px-2.5 py-1 text-xs font-bold text-blue-700">SOP {current.playbook_version}</span></div><h2 className="mt-3 text-xl font-bold">{current.external_order_id ?? current.external_case_id}</h2><p className="mt-1 text-sm text-slate-500">{current.marketplace_id} · {current.seller_sku ?? "未关联 SKU"} · {current.language}</p></div><div className="flex flex-wrap gap-2">{["open", "ready_to_send", "reopened"].includes(current.status) && <button onClick={() => transition.mutate("acknowledged")} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">认领</button>}{current.status === "acknowledged" && <button onClick={() => transition.mutate("in_progress")} className="rounded-lg border border-slate-300 px-3 py-2 text-xs font-semibold">开始处理</button>}{current.status === "awaiting_approval" && <Link href="/approvals" className="rounded-lg bg-amber-500 px-3 py-2 text-xs font-semibold text-white">前往审批</Link>}</div></div>
          <div className="mt-5 grid gap-3 md:grid-cols-4"><Metric icon={Clock3} label="SLA 截止" value={current.due_at ? new Date(current.due_at).toLocaleString("zh-CN") : "-"}/><Metric icon={MessageSquareText} label="负责人" value={current.assignee_id ?? "未认领"}/><Metric icon={ShieldAlert} label="风险" value={current.risk_level}/><Metric icon={CheckCircle2} label="置信度" value={current.confidence}/></div>
        </div>
        {current.operations_context && <div className="panel border-cyan-200 p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-bold text-cyan-700">跨团队根因整改</p><h3 className="mt-1 font-bold">{current.operations_context.cluster.root_cause_code} · {current.operations_context.cluster.team}</h3><p className="mt-1 text-sm text-slate-600">同 SKU 已确认 {current.operations_context.cluster.sample_count} / {current.operations_context.cluster.minimum_samples} 个 Case</p></div>{current.operations_context.work_item ? <Link href={`/work-items?view=active&work_item=${current.operations_context.work_item.id}`} className="rounded-lg bg-cyan-700 px-3 py-2 text-xs font-bold text-white">查看运营任务</Link> : <span className="rounded-lg bg-slate-100 px-3 py-2 text-xs font-semibold text-slate-600">证据收集中</span>}</div><div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500"><span>状态 {current.operations_context.cluster.status}</span><span>·</span><span>{current.operations_context.related_cases.length} 个关联 Case</span><span>·</span><span className={current.operations_context.cluster.verified_amazon_data ? "text-emerald-700" : "text-amber-700"}>{current.operations_context.cluster.verified_amazon_data ? "Amazon 已验证" : "未验证 / Mock 来源"}</span></div></div>}
        <div className="grid gap-5 lg:grid-cols-2">
          <div className="panel p-5"><h3 className="font-bold">买家消息</h3><p className="mt-3 rounded-xl bg-slate-50 p-4 text-sm leading-6 text-slate-700">{current.customer_message}</p><h3 className="mt-5 font-bold">订单与证据</h3><pre className="mt-3 max-h-72 overflow-auto rounded-xl bg-slate-950 p-4 text-xs leading-5 text-slate-200">{JSON.stringify(current.evidence, null, 2)}</pre></div>
          <div className="panel p-5"><div className="flex items-center justify-between"><h3 className="font-bold">回复草稿</h3><span className={`rounded-full px-2 py-1 text-xs font-bold ${current.policy_checks.decision === "block" ? "bg-rose-50 text-rose-700" : current.policy_checks.decision === "require_approval" ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700"}`}>{current.policy_checks.decision}</span></div><textarea value={reply} onChange={event => setReply(event.target.value)} className="mt-3 min-h-48 w-full rounded-xl border border-slate-300 p-4 text-sm leading-6"/><div className="mt-3 rounded-xl bg-slate-50 p-3 text-xs text-slate-600"><p className="font-semibold">Policy 结果</p><p className="mt-1">{current.policy_checks.reason}</p>{current.policy_checks.violations?.length ? <p className="mt-1 text-rose-700">违规项：{current.policy_checks.violations.join(", ")}</p> : null}</div>{current.status === "sent" && <label className="mt-4 block text-xs font-semibold text-slate-700">确认根因<select value={rootCause} onChange={event => setRootCause(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"><option value="unknown">证据不足 / 未知</option><option value="product_defect">商品质量</option><option value="size_or_fit">尺寸 / 适配</option><option value="shipping_damage">运输损坏</option><option value="packaging_problem">包装问题</option><option value="wrong_or_missing_item">错发 / 漏发</option><option value="instructions_unclear">使用说明不清</option><option value="listing_mismatch">Listing 描述不符</option><option value="subjective_dissatisfaction">主观不满意</option></select></label>}<div className="mt-4 flex gap-2">{["ready_to_send", "acknowledged", "in_progress"].includes(current.status) && current.policy_checks.decision !== "block" && <button onClick={() => send.mutate()} disabled={!reply.trim()} className="rounded-lg bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">Mock 发送</button>}{current.status === "sent" && <button onClick={() => resolve.mutate()} disabled={rootCause === "unknown" || resolve.isPending} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">确认根因并关闭</button>}</div></div>
        </div>
        <div className="panel p-5"><h3 className="font-bold">处理时间线</h3><div className="mt-4 space-y-3">{current.timeline?.map((item, index) => <div key={`${item.created_at}-${index}`} className="flex gap-3 border-l-2 border-slate-200 pl-4"><div><p className="text-sm font-semibold">{item.event_type}</p><p className="text-xs text-slate-500">{item.actor_id} · {new Date(item.created_at).toLocaleString("zh-CN")}</p></div></div>)}</div></div>
      </section>}
    </div>
  </div>;
}

function Metric({ icon: Icon, label, value }: { icon: typeof AlertTriangle; label: string; value: string }) { return <div className="rounded-xl bg-slate-50 p-3"><div className="flex items-center gap-2 text-xs text-slate-500"><Icon className="size-4"/>{label}</div><p className="mt-2 truncate text-sm font-semibold">{value}</p></div> }

function CasePagination({ page, pages, total, loading, onPageChange }: { page: number; pages: number; total: number; loading: boolean; onPageChange: (page: number) => void }) { return <div className="flex items-center justify-between border-t border-slate-200 p-3 text-xs text-slate-600"><span>{total} 个 · {pages === 0 ? 0 : page}/{pages} 页</span><div className="flex gap-2"><button type="button" aria-label="上一页客服 Case" title="上一页" onClick={() => onPageChange(Math.max(1, page - 1))} disabled={loading || page <= 1} className="grid size-9 place-items-center rounded-lg border border-slate-300 disabled:cursor-not-allowed disabled:opacity-40"><ChevronLeft className="size-4" /></button><button type="button" aria-label="下一页客服 Case" title="下一页" onClick={() => onPageChange(Math.min(pages, page + 1))} disabled={loading || pages === 0 || page >= pages} className="grid size-9 place-items-center rounded-lg border border-slate-300 disabled:cursor-not-allowed disabled:opacity-40"><ChevronRight className="size-4" /></button></div></div> }
