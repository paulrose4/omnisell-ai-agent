import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function POST() { return proxyBackend(await backendFetch("/api/mock-scenarios/operations-learning/seed", { method: "POST" })); }
