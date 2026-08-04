import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request, { params }: { params: Promise<{ issueId: string }> }) {
  const { issueId } = await params;
  return proxyBackend(await backendFetch(`/api/learning/issues/${encodeURIComponent(issueId)}/feedback`, {
    method: "POST",
    body: await request.text(),
  }));
}
