import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams.toString();
  return proxyBackend(await backendFetch("/api/advertising/diagnostics" + (query ? "?" + query : "")));
}
