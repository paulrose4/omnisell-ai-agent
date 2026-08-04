import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function POST(request: Request) {
  const idempotencyKey = request.headers.get("Idempotency-Key");
  return proxyBackend(await backendFetch("/api/mock-seller-central/execute", {
    method: "POST",
    headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    body: await request.text(),
  }));
}
