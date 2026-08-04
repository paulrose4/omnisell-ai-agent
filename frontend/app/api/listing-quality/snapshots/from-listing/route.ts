import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request) {
  return proxyBackend(await backendFetch("/api/listing-quality/snapshots/calculate-from-listing", { method: "POST", body: await request.text() }));
}
