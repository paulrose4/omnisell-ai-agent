import { ApprovalInbox } from "@/components/approvals/approval-inbox";

export default function ApprovalsPage() {
  return (
    <section className="mx-auto max-w-6xl">
      <div className="mb-7"><p className="eyebrow">Human in the loop</p><h1 className="mt-2 text-2xl font-bold tracking-tight text-slate-950 lg:text-3xl">人工审批工作台</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">审阅 Agent 的计划、执行依据与风险级别。批准内容会绑定 action hash，拒绝时请提供可执行的修正意见。</p></div>
      <ApprovalInbox />
    </section>
  );
}
