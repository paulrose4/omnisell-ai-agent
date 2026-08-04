import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request, context: { params: Promise<{ requestId: string }> }) {
  const { requestId } = await context.params;
  const payload = await request.json();
  return proxyBackend(
    await backendFetch(`/api/admin/approvals/${encodeURIComponent(requestId)}/reject`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}
