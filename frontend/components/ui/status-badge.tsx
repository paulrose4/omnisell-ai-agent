import { cn } from "@/lib/utils";

const styles = {
  pending: "bg-amber-50 text-amber-700 ring-amber-600/20",
  running: "bg-blue-50 text-blue-700 ring-blue-600/20",
  succeeded: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  failed: "bg-rose-50 text-rose-700 ring-rose-600/20",
  dead_letter: "bg-slate-100 text-slate-700 ring-slate-600/20",
  closed: "bg-slate-100 text-slate-500 ring-slate-400/20",
  low: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  medium: "bg-amber-50 text-amber-700 ring-amber-600/20",
  high: "bg-orange-50 text-orange-700 ring-orange-600/20",
  critical: "bg-rose-50 text-rose-700 ring-rose-600/20",
  received: "bg-slate-100 text-slate-700 ring-slate-600/20",
  validated: "bg-cyan-50 text-cyan-700 ring-cyan-600/20",
  evidence_collected: "bg-cyan-50 text-cyan-700 ring-cyan-600/20",
  analyzed: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
  output_validated: "bg-teal-50 text-teal-700 ring-teal-600/20",
  planned: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
  policy_evaluated: "bg-violet-50 text-violet-700 ring-violet-600/20",
  awaiting_approval: "bg-amber-50 text-amber-700 ring-amber-600/20",
  awaiting_second_approval: "bg-orange-50 text-orange-700 ring-orange-600/20",
  approved: "bg-teal-50 text-teal-700 ring-teal-600/20",
  executing: "bg-blue-50 text-blue-700 ring-blue-600/20",
  verifying: "bg-sky-50 text-sky-700 ring-sky-600/20",
  degraded: "bg-orange-50 text-orange-700 ring-orange-600/20",
  canceled: "bg-slate-100 text-slate-500 ring-slate-400/20",
};

export function StatusBadge({ value }: { value: keyof typeof styles }) {
  return <span className={cn("inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ring-1 ring-inset", styles[value])}>{value.replace("_", " ")}</span>;
}
