import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams.toString();
  return proxyBackend(await backendFetch("/api/voc/summaries" + (query ? "?" + query : "")));
}

export async function POST(request: Request) {
  return proxyBackend(await backendFetch("/api/voc/summaries", { method: "POST", body: await request.text() }));
}
