import { NextResponse } from "next/server";
import { auth } from "@/auth";
import {
  isBffRouteAllowed,
  isPageRouteAllowed,
  normalizeFrontendReleaseProfile,
} from "@/lib/api/release-profile-routes";

export const middleware = auth((request) => {
  const pathname = request.nextUrl.pathname;
  const configuredProfile = process.env.NEXT_PUBLIC_RELEASE_PROFILE ?? "full_development";
  const releaseProfile = normalizeFrontendReleaseProfile(configuredProfile);
  const publicPath = pathname.startsWith("/api/auth") || pathname === "/signin";
  if (!pathname.startsWith("/api/") && !publicPath && !isPageRouteAllowed(releaseProfile, pathname)) {
    return new NextResponse("Not Found", { status: 404 });
  }
  if (pathname.startsWith("/api/") && !publicPath && !isBffRouteAllowed(releaseProfile, request.method, pathname)) {
    return NextResponse.json(
      { error: "not_found", message: "Route not found for this release profile." },
      { status: 404 },
    );
  }
  const mockDisabled = process.env.NEXT_PUBLIC_MOCK_DRILL_ENABLED !== "true" || releaseProfile === "read_only_pilot";
  const mockPath = pathname.startsWith("/mock-") || pathname.startsWith("/api/mock-");
  if (mockDisabled && mockPath) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json(
        { error: "not_found", message: "Route not found." },
        { status: 404 },
      );
    }
    return new NextResponse("Not Found", { status: 404 });
  }

  const accessTokenExpiresAt = request.auth?.accessTokenExpiresAt;
  const accessTokenExpired = typeof accessTokenExpiresAt === "number" && accessTokenExpiresAt <= Date.now();
  const authenticated = Boolean(request.auth?.user && request.auth?.accessToken) && !accessTokenExpired;
  if (!publicPath && !authenticated) {
    if (pathname.startsWith("/api/")) {
      return NextResponse.json(
        { error: "authentication_required", message: "A valid company OIDC session is required." },
        { status: 401 },
      );
    }
    const signInUrl = new URL("/signin", request.nextUrl);
    signInUrl.searchParams.set("callbackUrl", `${pathname}${request.nextUrl.search}`);
    return NextResponse.redirect(signInUrl);
  }
  return NextResponse.next();
});

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
