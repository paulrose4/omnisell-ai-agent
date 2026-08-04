import NextAuth, { customFetch } from "next-auth";

if (process.env.OMNISELL_ENVIRONMENT === "production") {
  const required = ["AUTH_SECRET", "AUTH_OIDC_ISSUER", "AUTH_OIDC_CLIENT_ID", "AUTH_OIDC_CLIENT_SECRET", "BACKEND_API_URL"] as const;
  const missing = required.filter((name) => !process.env[name]?.trim());
  if (missing.length > 0) throw new Error(`Missing production frontend environment: ${missing.join(", ")}`);

  const placeholder = required.filter((name) => /change-me|replace-|generate-/i.test(process.env[name] ?? ""));
  if (placeholder.length > 0) throw new Error(`Placeholder production frontend environment: ${placeholder.join(", ")}`);
  if ((process.env.AUTH_SECRET?.length ?? 0) < 32) throw new Error("AUTH_SECRET must contain at least 32 characters");
  if (new URL(process.env.AUTH_OIDC_ISSUER as string).protocol !== "https:") throw new Error("AUTH_OIDC_ISSUER must use HTTPS in production");
}

const issuer = process.env.AUTH_OIDC_ISSUER;
const wellKnown = process.env.AUTH_OIDC_WELL_KNOWN_URL;
const internalBaseUrl = process.env.AUTH_OIDC_INTERNAL_BASE_URL;

const oidcBackchannelFetch: typeof fetch = async (input, init) => {
  if (!issuer || !internalBaseUrl) return fetch(input, init);
  const inputUrl = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const publicIssuer = new URL(issuer);
  const requestedUrl = new URL(inputUrl);
  if (requestedUrl.origin !== publicIssuer.origin) return fetch(input, init);
  const rewrittenUrl = new URL(`${requestedUrl.pathname}${requestedUrl.search}`, internalBaseUrl);
  return fetch(rewrittenUrl, init);
};

export const { handlers, auth, signIn, signOut } = NextAuth({
  trustHost: true,
  providers: issuer
    ? [{
        id: "oidc",
        name: "Company SSO",
        type: "oidc",
        issuer,
        wellKnown: wellKnown || undefined,
        [customFetch]: oidcBackchannelFetch,
        clientId: process.env.AUTH_OIDC_CLIENT_ID,
        clientSecret: process.env.AUTH_OIDC_CLIENT_SECRET,
        authorization: { params: { scope: "openid profile email" } },
      }]
    : [],
  session: { strategy: "jwt", maxAge: 60 * 60 * 8 },
  callbacks: {
    async jwt({ token, account }) {
      if (account?.access_token) token.accessToken = account.access_token;
      if (account?.expires_at) token.accessTokenExpiresAt = account.expires_at * 1000;
      return token;
    },
    async session({ session, token }) {
      session.accessToken = typeof token.accessToken === "string" ? token.accessToken : undefined;
      session.accessTokenExpiresAt = typeof token.accessTokenExpiresAt === "number" ? token.accessTokenExpiresAt : undefined;
      return session;
    },
    authorized({ auth, request }) {
      const publicPath = request.nextUrl.pathname.startsWith("/api/auth") || request.nextUrl.pathname === "/signin";
      return publicPath || Boolean(auth?.user && auth.accessToken);
    },
  },
  pages: { signIn: "/signin" },
});
