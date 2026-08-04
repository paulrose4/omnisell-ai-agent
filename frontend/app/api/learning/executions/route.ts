import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function POST(request: Request) {
  const idempotencyKey = request.headers.get("Idempotency-Key") ?? crypto.randomUUID();
  return proxyBackend(await backendFetch("/api/learning/executions", { method: "POST", headers: { "Idempotency-Key": idempotencyKey }, body: await request.text() }));
}
