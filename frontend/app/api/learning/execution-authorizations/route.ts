import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(request: Request) {
  return proxyBackend(await backendFetch(`/api/learning/execution-authorizations${new URL(request.url).search}`));
}

export async function POST(request: Request) {
  const idempotencyKey = request.headers.get("Idempotency-Key") ?? crypto.randomUUID();
  return proxyBackend(await backendFetch("/api/learning/execution-authorizations", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: await request.text(),
  }));
}
