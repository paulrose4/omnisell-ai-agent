import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(request: Request) {
  const query = new URL(request.url).search;
  return proxyBackend(await backendFetch(`/api/learning/activities${query}`));
}

export async function POST(request: Request) {
  const idempotencyKey = request.headers.get("Idempotency-Key") ?? crypto.randomUUID();
  return proxyBackend(await backendFetch("/api/learning/activities", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: await request.text(),
  }));
}
