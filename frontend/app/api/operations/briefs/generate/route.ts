import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request) {
  const body = await request.text();
  return proxyBackend(await backendFetch("/api/operations/briefs/generate", { method: "POST", body }));
}
