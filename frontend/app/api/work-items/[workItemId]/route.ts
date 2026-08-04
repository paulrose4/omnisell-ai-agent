import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(_: Request, context: { params: Promise<{ workItemId: string }> }) {
  const { workItemId } = await context.params;
  return proxyBackend(await backendFetch(`/api/work-items/${encodeURIComponent(workItemId)}`));
}
