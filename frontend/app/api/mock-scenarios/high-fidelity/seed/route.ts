import { backendFetch, proxyBackend } from "@/lib/api/server";

export async function POST(request: Request) {
  return proxyBackend(await backendFetch("/api/mock-scenarios/high-fidelity/seed", {
    method: "POST",
    body: await request.text(),
  }));
}
