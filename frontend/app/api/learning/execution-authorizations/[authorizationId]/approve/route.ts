import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(_: Request, context: { params: Promise<{ authorizationId: string }> }) {
  const { authorizationId } = await context.params;
  return proxyBackend(await backendFetch(`/api/learning/execution-authorizations/${authorizationId}/approve`, { method: "POST" }));
}
