import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(_: Request, { params }: { params: Promise<{ issueId: string }> }) {
  const { issueId } = await params;
  return proxyBackend(await backendFetch(`/api/operations/issues/${encodeURIComponent(issueId)}/detail`));
}
