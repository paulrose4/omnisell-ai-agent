import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request) {
  return proxyBackend(await backendFetch("/api/agent-runs/operations-recommendations", {
    method: "POST",
    body: await request.text(),
  }));
}
