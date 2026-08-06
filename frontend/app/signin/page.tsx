import { signIn } from "@/auth";

const mockPersonas = [
  {
    id: "local-owner",
    title: "Owner",
    description: "全局配置、成本策略与审计查看",
  },
  {
    id: "local-operator",
    title: "Operator",
    description: "认领异常、处理任务与回填执行结果",
  },
  {
    id: "local-approver",
    title: "Approver A",
    description: "第一位独立复核人，批准或拒绝高风险建议",
  },
  {
    id: "local-approver-2",
    title: "Approver B",
    description: "第二位独立复核人，用于 critical 双人审批",
  },
  {
    id: "local-viewer",
    title: "Viewer",
    description: "只读查看驾驶舱、异常与任务状态",
  },
] as const;

async function loginAsPersona(formData: FormData) {
  "use server";
  const persona = formData.get("persona");
  const allowedPersonas = mockPersonas.map((item) => item.id);
  if (typeof persona !== "string" || !allowedPersonas.includes(persona as (typeof allowedPersonas)[number])) {
    throw new Error("Unsupported local demo persona");
  }
  await signIn("oidc", { redirectTo: "/dashboard" }, { login_hint: persona });
}

async function loginWithCompanySso() {
  "use server";
  await signIn("oidc", { redirectTo: "/dashboard" });
}

export default function SignInPage() {
  const isMockOidc = process.env.AUTH_OIDC_ISSUER?.includes("localhost:9000")
    || process.env.AUTH_OIDC_ISSUER?.includes("mock-cloud:9000");

  return (
    <main className="grid min-h-screen place-items-center bg-slate-950 p-6">
      <section className="w-full max-w-2xl rounded-3xl bg-white p-8 shadow-2xl">
        <p className="text-sm font-semibold text-brand-600">OmniSell Amazon 运营演练</p>
        <h1 className="mt-2 text-3xl font-bold text-slate-950">运营人员登录</h1>
        <p className="mt-3 text-sm leading-6 text-slate-600">
          {isMockOidc
            ? "请选择本次演练身份。每个身份都会签发独立 JWT，并按真实 Membership、MFA 与 RBAC 规则鉴权。"
            : "使用公司 SSO 登录。生产环境要求身份提供商启用 MFA，所有操作将绑定到个人账户并进入审计。"}
        </p>

        {isMockOidc ? (
          <div className="mt-8 grid gap-3 sm:grid-cols-2">
            {mockPersonas.map((persona) => (
              <form key={persona.id} action={loginAsPersona}>
                <input type="hidden" name="persona" value={persona.id} />
                <button
                  type="submit"
                  className="h-full w-full rounded-2xl border border-slate-200 p-5 text-left transition hover:border-brand-500 hover:bg-brand-50 focus:outline-none focus:ring-2 focus:ring-brand-500"
                >
                  <span className="block text-base font-bold text-slate-950">{persona.title}</span>
                  <span className="mt-1 block text-sm leading-5 text-slate-600">{persona.description}</span>
                </button>
              </form>
            ))}
          </div>
        ) : (
          <form className="mt-8" action={loginWithCompanySso}>
            <button className="w-full rounded-xl bg-slate-950 px-4 py-3 font-semibold text-white hover:bg-slate-800">
              使用公司 SSO 登录
            </button>
          </form>
        )}

        {isMockOidc ? (
          <p className="mt-6 rounded-xl bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
            演练环境仍保持 Amazon 只读：批准仅代表允许运营人员手工执行，不会自动修改 Amazon。
          </p>
        ) : null}
      </section>
    </main>
  );
}
