"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, LoaderCircle } from "lucide-react";
import { apiFetch } from "@/lib/api/client";
import type { SKUHealthResponse } from "@/lib/api/types";
import { StatusBadge } from "@/components/ui/status-badge";

export function SKUHealthTable() {
  const query = useQuery({ queryKey: ["sku-health"], queryFn: () => apiFetch<SKUHealthResponse>("/api/operations/sku-health") });
  if (query.isLoading) return <div className="grid min-h-72 place-items-center"><LoaderCircle className="size-8 animate-spin text-brand-500" /></div>;
  if (query.error || !query.data) return <div className="panel p-6 text-sm text-rose-700">SKU 健康数据加载失败：{query.error?.message}</div>;
  return <section className="panel overflow-hidden"><div className="overflow-x-auto"><table className="min-w-full divide-y divide-slate-200 text-left text-sm"><thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">SKU</th><th className="px-5 py-3">健康度</th><th className="px-5 py-3">异常</th><th className="px-5 py-3">风险类型</th><th className="px-5 py-3">数据质量</th><th className="px-5 py-3">最近检测</th></tr></thead><tbody className="divide-y divide-slate-100 bg-white">{query.data.items.map((item) => <tr key={`${item.marketplace_id}-${item.seller_sku}`} className="hover:bg-slate-50"><td className="px-5 py-4"><p className="font-semibold text-slate-950">{item.seller_sku}</p><p className="mt-1 text-xs text-slate-500">{item.marketplace_id}</p></td><td className="px-5 py-4"><StatusBadge value={item.highest_severity} /></td><td className="px-5 py-4 font-semibold">{item.open_issue_count}</td><td className="max-w-sm px-5 py-4"><div className="flex flex-wrap gap-1.5">{item.issue_types.map((type) => <span key={type} className="rounded-lg bg-slate-100 px-2 py-1 text-xs text-slate-600">{type.replaceAll("_", " ")}</span>)}</div></td><td className="px-5 py-4">{item.has_data_quality_risk ? <span className="inline-flex items-center gap-1 text-amber-700"><AlertCircle className="size-4" />需核验</span> : <span className="text-emerald-700">正常</span>}</td><td className="whitespace-nowrap px-5 py-4 text-slate-500">{new Date(item.last_detected_at).toLocaleString("zh-CN")}</td></tr>)}</tbody></table></div>{!query.data.items.length && <div className="p-10 text-center text-sm text-slate-500">当前没有 SKU 异常。</div>}</section>;
}
