import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(_: Request, context: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await context.params;
  return proxyBackend(await backendFetch(`/api/customer-cases/${caseId}`));
}

export async function PATCH(request: Request, context: { params: Promise<{ caseId: string }> }) {
  const { caseId } = await context.params;
  return proxyBackend(await backendFetch(`/api/customer-cases/${caseId}`, { method: "PATCH", body: await request.text() }));
}
