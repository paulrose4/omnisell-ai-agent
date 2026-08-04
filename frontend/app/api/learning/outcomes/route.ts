import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function GET(request: Request) { return proxyBackend(await backendFetch(`/api/learning/outcomes${new URL(request.url).search}`)); }
export async function POST(request: Request) { return proxyBackend(await backendFetch("/api/learning/outcomes", { method: "POST", body: await request.text() })); }
