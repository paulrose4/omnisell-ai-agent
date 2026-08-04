import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request, context: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await context.params;
  return proxyBackend(await backendFetch(`/api/customer-cases/${caseId}/send`, { method: "POST", body: await request.text() }));
}
