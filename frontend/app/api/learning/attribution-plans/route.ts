import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(request: Request) {
  return proxyBackend(await backendFetch(`/api/learning/attribution-plans${new URL(request.url).search}`));
}

export async function POST(request: Request) {
  return proxyBackend(await backendFetch("/api/learning/attribution-plans", { method: "POST", body: await request.text() }));
}
