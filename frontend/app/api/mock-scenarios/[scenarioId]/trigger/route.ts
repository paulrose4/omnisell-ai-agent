import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function POST(request: Request, { params }: { params: Promise<{ scenarioId: string }> }) {
  const { scenarioId } = await params;
  return proxyBackend(await backendFetch(`/api/mock-scenarios/${scenarioId}/trigger`, { method: "POST", body: await request.text() }));
}
