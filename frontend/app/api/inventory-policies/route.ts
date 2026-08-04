import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function GET(request: Request){const q=new URL(request.url).searchParams.toString();return proxyBackend(await backendFetch(`/api/inventory/policies${q?`?${q}`:""}`));}
export async function POST(request: Request){return proxyBackend(await backendFetch("/api/inventory/policies",{method:"POST",body:await request.text()}));}