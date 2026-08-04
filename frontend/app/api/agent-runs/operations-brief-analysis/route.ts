import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request) {
  const idempotencyKey = request.headers.get("Idempotency-Key");
  return proxyBackend(await backendFetch("/api/agent-runs/operations-brief-analysis", {
    method: "POST",
    body: await request.text(),
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
  }));
}
