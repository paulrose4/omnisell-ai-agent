import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const search = url.searchParams.toString();
  return proxyBackend(await backendFetch(`/api/tasks${search ? `?${search}` : ""}`));
}
