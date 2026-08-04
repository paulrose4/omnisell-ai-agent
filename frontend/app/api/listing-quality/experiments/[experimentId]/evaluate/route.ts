import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request, { params }: { params: Promise<{ experimentId: string }> }) {
  const { experimentId } = await params;
  return proxyBackend(await backendFetch(`/api/listing-quality/experiments/${encodeURIComponent(experimentId)}/evaluate`, { method: "POST", body: await request.text() }));
}
