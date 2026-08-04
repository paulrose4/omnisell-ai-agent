import { OperationsDashboard } from "@/components/operations/operations-dashboard";

export default function DashboardPage() {
  return <section className="mx-auto max-w-7xl"><div className="mb-7"><p className="eyebrow">Amazon operations control tower</p><h1 className="mt-2 text-2xl font-bold tracking-tight text-slate-950 lg:text-3xl">经营总览</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">汇总利润、库存、Listing 和数据质量异常，按严重程度与量化影响生成今日优先事项。</p></div><OperationsDashboard /></section>;
}
