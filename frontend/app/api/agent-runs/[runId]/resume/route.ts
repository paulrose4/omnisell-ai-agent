import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(_request: Request, { params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  return proxyBackend(await backendFetch(`/api/agent-runs/${encodeURIComponent(runId)}/resume`, {
    method: "POST",
  }));
}
