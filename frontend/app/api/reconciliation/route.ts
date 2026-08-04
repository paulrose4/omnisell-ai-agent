import { backendFetch, proxyBackend } from "@/lib/api/server";
export async function GET(){return proxyBackend(await backendFetch("/api/reconciliation/runs"));}
export async function POST(request:Request){const key=request.headers.get("Idempotency-Key");return proxyBackend(await backendFetch("/api/reconciliation/runs",{method:"POST",body:await request.text(),headers:key?{"Idempotency-Key":key}:undefined}));}
