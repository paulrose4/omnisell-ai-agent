import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET() {
  return proxyBackend(await backendFetch("/api/operations/sku-health"));
}
