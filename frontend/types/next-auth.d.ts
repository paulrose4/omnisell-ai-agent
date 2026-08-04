import "next-auth";

declare module "next-auth" {
  interface Session {
    accessToken?: string;
    accessTokenExpiresAt?: number;
  }
}

declare module "@auth/core/jwt" {
  interface JWT {
    accessToken?: string;
    accessTokenExpiresAt?: number;
  }
}