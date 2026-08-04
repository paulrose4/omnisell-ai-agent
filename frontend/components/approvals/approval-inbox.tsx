"use client";

import { useQuery } from "@tanstack/react-query";
import { Inbox, LoaderCircle, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api/client";
import type { ApprovalListResponse } from "@/lib/api/types";
import { ApprovalCard } from "./approval-card";

export function ApprovalInbox() {
  const query = useQuery({
    queryKey: ["approvals"],
    queryFn: () => apiFetch<ApprovalListResponse>("/api/approvals"),
    refetchInterval: Number(process.env.NEXT_PUBLIC_REFRESH_INTERVAL_MS ?? 5000),
  });

  if (query.isLoading) return <div className="panel grid min-h-64 place-items-center"><LoaderCircle className="size-7 animate-spin text-brand-500" /></div>;
  if (query.error) return <div className="panel p-6"><p className="font-semibold text-rose-700">审批数据加载失败</p><p className="mt-1 text-sm text-slate-600">{query.error.message}</p></div>;

  const items = query.data?.items ?? [];
  const secondStageCount = items.filter((item) => item.status === "awaiting_second_approval").length;
  return (
    <div>
      <div className="mb-5 flex items-center justify-between">
        <p className="text-sm text-slate-500">{items.length} 项待处理 · {secondStageCount} 项待二审</p>
        <button onClick={() => query.refetch()} className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"><RefreshCw className={`size-4 ${query.isFetching ? "animate-spin" : ""}`} />刷新</button>
      </div>
      {items.length ? <div className="space-y-4">{items.map((item) => <ApprovalCard key={item.request_id} approval={item} />)}</div> : (
        <div className="panel grid min-h-72 place-items-center p-8 text-center"><div><div className="mx-auto grid size-12 place-items-center rounded-2xl bg-emerald-50"><Inbox className="size-6 text-emerald-600" /></div><h2 className="mt-4 font-semibold">审批 Inbox 已清空</h2><p className="mt-1 text-sm text-slate-500">当前没有等待人工处理的高风险动作。</p></div></div>
      )}
    </div>
  );
}
