import "server-only";

import { auth } from "@/auth";
export async function backendAuthHeaders(): Promise<HeadersInit> {
  const session = await auth();
  if (!session?.accessToken) throw new Error("Authenticated OIDC access token is required");
  if (session.accessTokenExpiresAt && session.accessTokenExpiresAt <= Date.now()) throw new Error("OIDC access token has expired");
  return { Authorization: `Bearer ${session.accessToken}` };
}
export async function backendFetch(path: string, init?: RequestInit): Promise<Response> {
  const baseUrl = process.env.BACKEND_API_URL ?? "http://localhost:8080";
  return fetch(`${baseUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(await backendAuthHeaders()), ...init?.headers },
    cache: "no-store",
  });
}

export async function proxyBackend(response: Response): Promise<Response> {
  const body = await response.text();
  return new Response(body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") ?? "application/json" },
  });
}
