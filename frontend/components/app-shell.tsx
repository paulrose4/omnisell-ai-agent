"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  Boxes,
  ClipboardCheck,
  Cloud,
  Coins,
  FlaskConical,
  Gauge,
  Headphones,
  LayoutDashboard,
  ListChecks,
  BarChart3,
  LogOut,
  Menu,
  MessageSquareMore,
  PackageSearch,
  Radio,
  Scale,
  ShieldCheck,
  Store,
  TriangleAlert,
  WandSparkles,
  X,
} from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api/client";
import { normalizeFrontendReleaseProfile } from "@/lib/api/release-profile-routes";

type SessionContext = {
  tenant_id: string;
  user_id: string;
  role: string;
  scopes: string[];
  environment: string;
  amazon_writes_enabled: boolean;
  amazon_pilot_read_only: boolean;
};

const baseNav = [
  { href: "/dashboard", label: "经营总览", icon: Gauge },
  { href: "/sku-health", label: "SKU 健康度", icon: Boxes },
  { href: "/connections", label: "Amazon 连接", icon: Cloud },
  { href: "/costs", label: "成本导入", icon: Coins },
  { href: "/inventory-policies", label: "库存策略", icon: PackageSearch },
  { href: "/issues", label: "异常处理", icon: TriangleAlert },
  { href: "/work-items", label: "运营任务", icon: ListChecks },
  { href: "/intelligence", label: "广告与 Amazon 异常", icon: BarChart3 },
  { href: "/customer-cases", label: "客服 Case 360", icon: Headphones },
  { href: "/listing-quality", label: "Listing 实验", icon: WandSparkles },
  { href: "/reconciliation", label: "每日对账", icon: Scale },
  { href: "/agents", label: "Agent 监控", icon: Radio },
  { href: "/approvals", label: "审批工作台", icon: ClipboardCheck },
];

const mockNav = [
  { href: "/mock-scenarios", label: "场景控制台", icon: FlaskConical },
  { href: "/mock-seller-central", label: "Mock Seller Central", icon: Store },
  { href: "/mock-customer-service", label: "Mock 客服场景", icon: MessageSquareMore },
];

const goldenDemoNav = [
  { href: "/dashboard", label: "经营总览", icon: Gauge },
  { href: "/issues", label: "异常处理", icon: TriangleAlert },
  { href: "/approvals", label: "审批工作台", icon: ClipboardCheck },
  { href: "/mock-seller-central", label: "Mock Seller Central", icon: Store },
  { href: "/agents", label: "Agent 监控", icon: Radio },
];

export function AppShell({ children, email, mockEnabled, environment, releaseProfile, signOutAction }: { children: React.ReactNode; email?: string | null; mockEnabled: boolean; environment: string; releaseProfile: string; signOutAction: () => Promise<void> }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const firstLink = useRef<HTMLAnchorElement>(null);
  const context = useQuery({ queryKey: ["session-context"], queryFn: () => apiFetch<SessionContext>("/api/session-context"), staleTime: 60_000 });
  const normalizedReleaseProfile = normalizeFrontendReleaseProfile(releaseProfile);
  const goldenDemo = normalizedReleaseProfile === "golden_demo";
  const mockAvailable = mockEnabled && normalizedReleaseProfile !== "read_only_pilot";
  const nav = goldenDemo
    ? goldenDemoNav.filter((item) => item.href !== "/mock-seller-central" || mockAvailable)
    : (mockAvailable ? [...baseNav, ...mockNav] : baseNav);
  const production = environment === "production";
  const readOnly = context.data?.amazon_pilot_read_only !== false || context.data?.amazon_writes_enabled !== true;

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    firstLink.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", closeOnEscape);
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener("keydown", closeOnEscape); };
  }, [open]);

  return <div className="min-h-screen lg:grid lg:grid-cols-[256px_minmax(0,1fr)]">
    {open && <button className="fixed inset-0 z-40 bg-slate-950/50 lg:hidden" onClick={() => setOpen(false)} aria-label="关闭导航遮罩" />}
    <aside className={`fixed inset-y-0 left-0 z-50 flex w-[min(288px,88vw)] flex-col border-r border-slate-800 bg-slate-950 text-white transition-transform duration-200 lg:static lg:z-auto lg:w-auto lg:min-h-screen lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`} aria-label="主导航">
      <div className="flex h-16 shrink-0 items-center gap-3 border-b border-slate-800 px-5"><div className="grid size-9 place-items-center rounded-lg bg-cyan-600"><Bot className="size-5" /></div><div className="min-w-0 flex-1"><p className="font-semibold">OmniSell</p><p className="text-xs text-slate-400">Control Center</p></div><button onClick={() => setOpen(false)} className="grid size-9 place-items-center rounded-lg text-slate-300 hover:bg-slate-800 lg:hidden" aria-label="关闭导航"><X className="size-5" /></button></div>
      <nav className="min-h-0 flex-1 overflow-y-auto p-3 lg:p-4">{nav.map(({ href, label, icon: Icon }, index) => { const active = pathname === href || pathname.startsWith(`${href}/`); return <a ref={index === 0 ? firstLink : undefined} key={href} href={href} className={`mb-1 flex min-h-10 items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition ${active ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-900 hover:text-white"}`}><Icon className="size-4 shrink-0" /><span className="min-w-0 break-words">{label}</span></a>; })}</nav>
      <div className="shrink-0 border-t border-slate-800 p-4"><div className="flex items-center gap-2 text-sm font-medium"><ShieldCheck className="size-4 text-emerald-400" /><span className="truncate">{context.data?.tenant_id ?? "租户上下文加载中"}</span></div><p className="mt-1 truncate text-xs text-slate-400">{context.data?.role ?? "-"} · {readOnly ? "Amazon 只读" : "Amazon 写入启用"}</p><form action={signOutAction} className="mt-3"><button className="inline-flex items-center gap-2 text-xs font-semibold text-slate-300 hover:text-white"><LogOut className="size-4" />退出登录</button></form></div>
    </aside>
    <main className="min-w-0 overflow-x-clip">
      <header className="sticky top-0 z-30 flex min-h-16 items-center justify-between gap-2 border-b border-slate-200 bg-white/95 px-3 backdrop-blur sm:px-5 lg:px-8"><div className="flex min-w-0 items-center gap-2"><button onClick={() => setOpen(true)} className="grid size-10 shrink-0 place-items-center rounded-lg border border-slate-300 text-slate-700 lg:hidden" aria-label="打开主导航" aria-expanded={open}><Menu className="size-5" /></button><div className="hidden items-center gap-2 text-sm text-slate-500 sm:flex"><LayoutDashboard className="size-4" /><span>运营控制台</span></div><span className="max-w-[116px] truncate text-xs font-medium text-slate-600 sm:hidden">{context.data?.tenant_id ?? "OmniSell"}</span></div><div className="flex min-w-0 items-center gap-2"><span className={`shrink-0 rounded-full px-2.5 py-1.5 text-xs font-bold ${production ? "bg-rose-50 text-rose-700" : "bg-cyan-50 text-cyan-800"}`}>{production ? (readOnly ? "生产 · 只读" : "生产") : "非生产"}</span><span className="hidden max-w-48 truncate text-xs text-slate-500 md:inline">{context.data?.tenant_id} · {email}</span></div></header>
      <div className="min-w-0 px-4 py-5 sm:p-5 lg:p-8">{children}</div>
    </main>
  </div>;
}
