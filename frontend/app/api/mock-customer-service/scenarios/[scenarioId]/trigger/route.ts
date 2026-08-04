import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request, context: { params: Promise<{ scenarioId: string }> }) {
  const { scenarioId } = await context.params;
  return proxyBackend(await backendFetch(`/api/mock-customer-service/scenarios/${scenarioId}/trigger`, { method: "POST", body: await request.text() }));
}
