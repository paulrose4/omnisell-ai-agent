"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, Send, X } from "lucide-react";
import { useState } from "react";
import { apiFetch } from "@/lib/api/client";
import type { ApprovalDecisionInput, ApprovalRequest } from "@/lib/api/types";

interface ApprovalDecisionPanelProps {
  approval: ApprovalRequest;
  onClose: () => void;
}

export function ApprovalDecisionPanel({ approval, onClose }: ApprovalDecisionPanelProps) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"approve" | "reject">("approve");
  const [reason, setReason] = useState("");
  const mutation = useMutation({
    mutationFn: () => apiFetch<ApprovalRequest>(`/api/approvals/${approval.request_id}/${mode}`, {
      method: "POST",
      body: JSON.stringify({ reason } satisfies ApprovalDecisionInput),
    }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["approvals"] }),
        queryClient.invalidateQueries({ queryKey: ["agent-runs"] }),
        queryClient.invalidateQueries({ queryKey: ["issues"] }),
      ]);
      onClose();
    },
  });

  const reasonRequired = approval.risk_level === "high" || approval.risk_level === "critical" || mode === "reject";
  const canSubmit = !reasonRequired || reason.trim().length >= 4;
  const secondStage = approval.status === "awaiting_second_approval";

  return <div className="mt-5 border-t border-slate-200 pt-5">
    <div className="grid grid-cols-2 gap-1 rounded-lg bg-slate-100 p-1">
      <button onClick={() => setMode("approve")} className={`flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition ${mode === "approve" ? "bg-white text-emerald-700 shadow-sm" : "text-slate-600"}`}><Check className="size-4" />批准</button>
      <button onClick={() => setMode("reject")} className={`flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-semibold transition ${mode === "reject" ? "bg-white text-rose-700 shadow-sm" : "text-slate-600"}`}><X className="size-4" />拒绝</button>
    </div>
    <label className="mt-4 block text-sm font-medium text-slate-700">{mode === "approve" ? "审批依据" : "拒绝原因"}<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={3} placeholder={mode === "approve" ? "记录核验过的证据、约束和批准理由" : "记录不批准的证据和修正要求"} className="mt-2 w-full resize-none rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-cyan-600 focus:ring-2 focus:ring-cyan-100" /></label>
    {secondStage && mode === "approve" && <p className="mt-2 text-xs text-slate-500">本次将作为第二名已登录审批人的独立决定。</p>}
    {mutation.error && <p className="mt-3 bg-rose-50 px-3 py-2 text-sm text-rose-700">{mutation.error.message}</p>}
    <div className="mt-4 flex justify-end gap-2"><button onClick={onClose} className="rounded-lg px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-100">取消</button><button disabled={!canSubmit || mutation.isPending} onClick={() => mutation.mutate()} className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50 ${mode === "approve" ? "bg-emerald-700" : "bg-rose-700"}`}>{mutation.isPending ? <LoaderCircle className="size-4 animate-spin" /> : <Send className="size-4" />}{mode === "approve" ? (secondStage ? "提交第二次批准" : "提交批准") : "提交拒绝"}</button></div>
  </div>;
}
