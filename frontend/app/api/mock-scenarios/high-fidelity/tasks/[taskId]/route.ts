import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function GET(_: Request, { params }: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await params;
  return proxyBackend(await backendFetch(`/api/mock-scenarios/high-fidelity/tasks/${taskId}`));
}
