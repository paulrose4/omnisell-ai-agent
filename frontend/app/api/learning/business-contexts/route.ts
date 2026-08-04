import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function POST(request: Request) { return proxyBackend(await backendFetch("/api/learning/business-contexts", { method: "POST", body: await request.text() })); }
