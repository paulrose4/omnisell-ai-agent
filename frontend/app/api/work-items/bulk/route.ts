import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request) {
  return proxyBackend(await backendFetch("/api/work-items/bulk", {
    method: "POST",
    body: await request.text(),
  }));
}
