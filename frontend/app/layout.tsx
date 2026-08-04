import type { Metadata } from "next";
import { auth, signOut } from "@/auth";
import { AppShell } from "@/components/app-shell";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "OmniSell Control Center",
  description: "Agent operations and HITL approval console",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const session = await auth();
  async function signOutAction() {
    "use server";
    await signOut({ redirectTo: "/signin" });
  }
  return <html lang="zh-CN"><body className="min-h-screen antialiased"><Providers><AppShell email={session?.user?.email} mockEnabled={process.env.NEXT_PUBLIC_MOCK_DRILL_ENABLED === "true"} environment={process.env.NEXT_PUBLIC_ENVIRONMENT ?? "development"} signOutAction={signOutAction}>{children}</AppShell></Providers></body></html>;
}
