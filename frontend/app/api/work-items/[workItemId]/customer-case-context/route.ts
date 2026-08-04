import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(_: Request, { params }: { params: Promise<{ workItemId: string }> }) {
  const { workItemId } = await params;
  return proxyBackend(await backendFetch(`/api/work-items/${encodeURIComponent(workItemId)}/customer-case-context`));
}
