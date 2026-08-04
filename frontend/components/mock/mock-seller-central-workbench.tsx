"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { apiFetch } from "@/lib/api/client";
import type { OperationalIssue } from "@/lib/api/types";

type Connection = { id: string; status: string };
const actionFields: Record<string, { label: string; key: string; placeholder: string }> = {
  create_replenishment_plan: { label: "补货数量", key: "inbound_quantity", placeholder: "600" },
  update_listing_title: { label: "新标题", key: "title", placeholder: "New compliant product title" },
  adjust_fbm_inventory: { label: "FBM 可售库存", key: "available", placeholder: "50" },
  update_price: { label: "新价格", key: "price", placeholder: "29.99" },
  mark_not_executed: { label: "未执行原因", key: "reason", placeholder: "说明不执行原因" },
};

export function MockSellerCentralWorkbench() {
  const client = useQueryClient();
  const [issueId, setIssueId] = useState("");
  const [actionType, setActionType] = useState("create_replenishment_plan");
  const [value, setValue] = useState("600");
  const [notes, setNotes] = useState("");
  const issues = useQuery({ queryKey: ["issues", "approved_for_manual_execution"], queryFn: () => apiFetch<{ items: OperationalIssue[] }>("/api/issues?status=approved_for_manual_execution"), refetchInterval: 5000 });
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => apiFetch<{ items: Connection[] }>("/api/connections") });
  const connectionId = useMemo(() => connections.data?.items.find(item => item.status === "active")?.id, [connections.data]);
  const selected = issues.data?.items.find(item => item.id === issueId);
  const execute = useMutation({
    mutationFn: async () => {
      if (!connectionId || !selected) throw new Error("请选择已批准异常");
      const field = actionFields[actionType];
      const numeric = field.key === "inbound_quantity" || field.key === "available";
      return apiFetch<{ execution_id: string; issue_status: string }>("/api/mock-seller-central/execute", {
        method: "POST",
        body: JSON.stringify({ connection_id: connectionId, issue_id: selected.id, action_type: actionType, changes: { [field.key]: numeric ? Number(value) : value }, notes, execution_evidence: { source: "mock_seller_central_ui" } }),
      });
    },
    onSuccess: async () => { setIssueId(""); await client.invalidateQueries({ queryKey: ["issues"] }); await client.invalidateQueries({ queryKey: ["operations-dashboard"] }); },
  });
  const field = actionFields[actionType];
  return <div className="space-y-6">
    <div><p className="eyebrow">Manual execution simulator</p><h1 className="mt-2 text-3xl font-bold">Mock Seller Central</h1><p className="mt-2 text-sm text-slate-600">模拟运营人员离开 OmniSell、在 Seller Central 手工执行，再由系统重新同步验证。</p></div>
    <div className="rounded-2xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900"><ShieldAlert className="mr-2 inline size-4"/>这里只写入 Mock Amazon。OmniSell 的真实 Amazon Adapter 写操作保持关闭。</div>
    <section className="panel max-w-3xl p-6"><div className="grid gap-5 md:grid-cols-2"><label className="text-sm font-semibold">已批准异常<select value={issueId} onChange={event => setIssueId(event.target.value)} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal"><option value="">请选择</option>{issues.data?.items.map(item => <option key={item.id} value={item.id}>{item.seller_sku} · {item.title}</option>)}</select></label><label className="text-sm font-semibold">模拟操作<select value={actionType} onChange={event => { setActionType(event.target.value); setValue(""); }} className="mt-2 w-full rounded-xl border border-slate-300 bg-white px-3 py-2.5 font-normal">{Object.keys(actionFields).map(item => <option key={item} value={item}>{item}</option>)}</select></label></div>
      {selected && <div className="mt-5 rounded-xl bg-slate-50 p-4 text-sm"><p className="font-semibold">{selected.seller_sku}</p><p className="mt-1 text-slate-600">{selected.summary}</p><p className="mt-2 text-xs text-slate-500">风险：{selected.severity} · 数据时间：{new Date(selected.last_detected_at).toLocaleString("zh-CN")}</p></div>}
      <label className="mt-5 block text-sm font-semibold">{field.label}<input value={value} onChange={event => setValue(event.target.value)} placeholder={field.placeholder} className="mt-2 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal"/></label><label className="mt-5 block text-sm font-semibold">操作备注<textarea value={notes} onChange={event => setNotes(event.target.value)} className="mt-2 min-h-24 w-full rounded-xl border border-slate-300 px-3 py-2.5 font-normal"/></label>
      <button onClick={() => execute.mutate()} disabled={!selected || !connectionId || execute.isPending} className="mt-5 inline-flex items-center gap-2 rounded-xl bg-slate-950 px-5 py-3 text-sm font-semibold text-white disabled:opacity-40"><ExternalLink className="size-4"/>{execute.isPending ? "提交并等待同步" : "在 Mock Seller Central 执行"}</button>
      {execute.isSuccess && <p className="mt-4 flex items-center gap-2 text-sm font-semibold text-emerald-700"><CheckCircle2 className="size-4"/>已记录执行，系统正在重新同步与验证。</p>}
      {execute.isError && <p className="mt-4 text-sm text-rose-700">{execute.error.message}</p>}
    </section>
  </div>;
}
