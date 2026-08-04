"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, Fingerprint, ShieldAlert, Users } from "lucide-react";
import { useState } from "react";
import { apiFetch } from "@/lib/api/client";
import type { ApprovedManualExecutionListResponse } from "@/lib/api/types";

function newIdempotencyKey() {
  return `manual-execution-${crypto.randomUUID()}`;
}

export function MockSellerCentralWorkbench() {
  const client = useQueryClient();
  const [approvalRequestId, setApprovalRequestId] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [notes, setNotes] = useState("");
  const plans = useQuery({
    queryKey: ["approved-manual-execution-plans"],
    queryFn: () => apiFetch<ApprovedManualExecutionListResponse>("/api/mock-seller-central/approved-plans"),
    refetchInterval: 5000,
  });
  const selected = plans.data?.items.find(item => item.approval_request_id === approvalRequestId);
  const execute = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("请选择已完成审批的执行计划");
      const stableKey = idempotencyKey || newIdempotencyKey();
      if (!idempotencyKey) setIdempotencyKey(stableKey);
      return apiFetch<{ execution_id: string; issue_status: string }>("/api/mock-seller-central/execute", {
        method: "POST",
        headers: { "Idempotency-Key": stableKey },
        body: JSON.stringify({ approval_request_id: selected.approval_request_id, notes, execution_evidence: { source: "mock_seller_central_ui" } }),
      });
    },
    onSuccess: async () => {
      setApprovalRequestId("");
      setIdempotencyKey("");
      setNotes("");
      await client.invalidateQueries({ queryKey: ["approved-manual-execution-plans"] });
      await client.invalidateQueries({ queryKey: ["issues"] });
      await client.invalidateQueries({ queryKey: ["operations-dashboard"] });
    },
  });
  return <div className="space-y-6">
    <div><p className="eyebrow">Manual execution simulator</p><h1 className="mt-2 text-3xl font-bold">Mock Seller Central</h1><p className="mt-2 text-sm text-slate-600">模拟运营人员离开 OmniSell、在 Seller Central 手工执行，再由系统重新同步验证。</p></div>
    <div className="rounded-2xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900"><ShieldAlert className="mr-2 inline size-4"/>这里只写入 Mock Amazon。OmniSell 的真实 Amazon Adapter 写操作保持关闭。</div>
    <section className="panel max-w-3xl p-6">
      <label className="text-sm font-semibold">已完成审批的执行计划<select value={approvalRequestId} onChange={event => { setApprovalRequestId(event.target.value); setIdempotencyKey(event.target.value ? newIdempotencyKey() : ""); }} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="">请选择</option>{plans.data?.items.map(item => <option key={item.approval_request_id} value={item.approval_request_id}>{item.issue.seller_sku} · {item.recommended_action.title}</option>)}</select></label>
      {!plans.isLoading && plans.data?.total === 0 && <p className="mt-4 text-sm text-slate-500">当前没有完成审批且尚未执行的计划。</p>}
      {selected && <div className="mt-5 space-y-4 border-y border-slate-200 py-5 text-sm">
        <div><p className="font-semibold">{selected.issue.seller_sku} · {selected.issue.title}</p><p className="mt-1 text-slate-600">{selected.issue.summary}</p><p className="mt-2 text-xs text-slate-500">Marketplace：{selected.issue.marketplace_id} · 证据时间：{new Date(selected.issue.data_freshness_at).toLocaleString("zh-CN")}</p></div>
        <dl className="grid gap-3 sm:grid-cols-2"><div><dt className="text-xs text-slate-500">批准动作</dt><dd className="mt-1 font-semibold">{selected.execution_plan.action_type}</dd></div><div><dt className="text-xs text-slate-500">批准补货量</dt><dd className="mt-1 font-semibold">{selected.execution_plan.changes.inbound_quantity.toLocaleString("zh-CN")} 件</dd></div><div><dt className="text-xs text-slate-500">绑定连接</dt><dd className="mt-1 break-all font-mono text-xs">{selected.execution_plan.connection_id}</dd></div><div><dt className="text-xs text-slate-500">审批失效时间</dt><dd className="mt-1">{new Date(selected.expires_at).toLocaleString("zh-CN")}</dd></div></dl>
        <div className="flex items-start gap-2 text-xs text-slate-600"><Fingerprint className="mt-0.5 size-4 shrink-0"/><code className="break-all">{selected.action_hash}</code></div>
        <div className="flex items-center gap-2 text-xs text-slate-600"><Users className="size-4"/><span>{selected.approvals.first_approver_id} · {selected.approvals.second_approver_id}</span></div>
      </div>}
      <label className="mt-5 block text-sm font-semibold">操作备注<textarea value={notes} onChange={event => setNotes(event.target.value)} className="mt-2 min-h-24 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal"/></label>
      <button onClick={() => execute.mutate()} disabled={!selected || execute.isPending} className="mt-5 inline-flex items-center gap-2 rounded-xl bg-slate-950 px-5 py-3 text-sm font-semibold text-white disabled:opacity-40"><ExternalLink className="size-4"/>{execute.isPending ? "提交并等待同步" : "执行已批准计划"}</button>
      {execute.isSuccess && <p className="mt-4 flex items-center gap-2 text-sm font-semibold text-emerald-700"><CheckCircle2 className="size-4"/>已记录执行，系统正在重新同步与验证。</p>}
      {execute.isError && <p className="mt-4 text-sm text-rose-700">{execute.error.message}</p>}
    </section>
  </div>;
}
