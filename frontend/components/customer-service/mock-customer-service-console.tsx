"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api/client";

type Scenario = { scenario_id: string; message: string; language: string };

export function MockCustomerServiceConsole() {
  const client = useQueryClient();
  const [orderId, setOrderId] = useState("MOCK-MOCK-STOCKOUT-00");
  const scenarios = useQuery({ queryKey: ["mock-customer-scenarios"], queryFn: () => apiFetch<{items: Scenario[]}>("/api/mock-customer-service/scenarios") });
  const trigger = useMutation({ mutationFn: (scenarioId: string) => apiFetch(`/api/mock-customer-service/scenarios/${scenarioId}/trigger`, { method: "POST", body: JSON.stringify({ external_order_id: orderId }) }), onSuccess: () => client.invalidateQueries({ queryKey: ["customer-cases"] }) });
  return <div className="space-y-6"><div><p className="eyebrow">Mock buyer messaging</p><h1 className="mt-2 text-3xl font-bold">Mock 客服场景控制台</h1><p className="mt-2 text-sm text-slate-600">一键生成买家消息，验证分类、订单证据、SOP、Policy、审批与 Case 360。所有数据均为 Mock。</p></div><div className="panel p-5"><label className="text-sm font-semibold">关联订单号</label><input value={orderId} onChange={event => setOrderId(event.target.value)} className="mt-2 w-full max-w-lg rounded-xl border border-slate-300 px-4 py-2"/><div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">{scenarios.data?.items.map(item => <article key={item.scenario_id} className="rounded-2xl border border-slate-200 p-4"><div className="flex items-center justify-between"><h2 className="font-bold">{item.scenario_id}</h2><span className="text-xs text-slate-400">{item.language}</span></div><p className="mt-3 min-h-16 text-sm leading-6 text-slate-600">{item.message}</p><button onClick={() => trigger.mutate(item.scenario_id)} disabled={!orderId.trim() || trigger.isPending} className="mt-4 rounded-lg bg-slate-950 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">触发买家消息</button></article>)}</div><Link href="/customer-cases" className="mt-5 inline-flex rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white">打开 Case 360</Link></div></div>;
}
