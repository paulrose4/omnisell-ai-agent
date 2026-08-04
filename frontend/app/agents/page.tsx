import { AgentDashboard } from "@/components/agents/agent-dashboard";

export default function AgentsPage() {
  return (
    <section className="mx-auto max-w-7xl">
      <div className="mb-7"><p className="eyebrow">Agent operations</p><h1 className="mt-2 text-2xl font-bold tracking-tight text-slate-950 lg:text-3xl">Agent 监控大盘</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">分别查看业务 Agent Run 与 Celery 同步任务，追踪状态机、证据、审批、执行结果和数据新鲜度。</p></div>
      <AgentDashboard />
    </section>
  );
}
