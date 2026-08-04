import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request, { params }: { params: Promise<{ issueId: string }> }) {
  const { issueId } = await params;
  return proxyBackend(await backendFetch(`/api/operations/issues/${encodeURIComponent(issueId)}/approval-requests`, {
    method: "POST",
    body: await request.text(),
  }));
}
