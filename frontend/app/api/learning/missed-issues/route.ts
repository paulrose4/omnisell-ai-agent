import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function GET() { return proxyBackend(await backendFetch("/api/learning/missed-issues")); }
export async function POST(request: Request) { return proxyBackend(await backendFetch("/api/learning/missed-issues", { method: "POST", body: await request.text() })); }
