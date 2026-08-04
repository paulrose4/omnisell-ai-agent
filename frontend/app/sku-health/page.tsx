import { SKUHealthTable } from "@/components/operations/sku-health-table";

export default function SKUHealthPage() {
  return <section className="mx-auto max-w-7xl"><div className="mb-7"><p className="eyebrow">SKU health</p><h1 className="mt-2 text-2xl font-bold tracking-tight text-slate-950 lg:text-3xl">SKU 健康度</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">按 SKU 查看断货、积压、贡献亏损、Listing 和数据质量风险。金额仅在币种明确时汇总。</p></div><SKUHealthTable /></section>;
}
