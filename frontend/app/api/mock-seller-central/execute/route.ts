import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function POST(request: Request) { return proxyBackend(await backendFetch("/api/mock-seller-central/execute", { method: "POST", body: await request.text() })); }
