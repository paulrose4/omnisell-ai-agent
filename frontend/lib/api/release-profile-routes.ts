import profileManifest from "@/generated/bff-profile-manifest.json";

export type ReleaseProfile = "full_development" | "golden_demo" | "read_only_pilot";

const goldenDemoPagePrefixes = ["/dashboard", "/issues", "/approvals", "/mock-seller-central", "/agents"];

const aliases: Record<string, ReleaseProfile> = {
  full: "full_development",
  interview: "golden_demo",
};

export function normalizeFrontendReleaseProfile(value: string | undefined): ReleaseProfile {
  const normalized = (value ?? "full_development").trim().toLowerCase();
  const profile = aliases[normalized] ?? normalized;
  if (profile === "full_development" || profile === "golden_demo" || profile === "read_only_pilot") {
    return profile;
  }
  return "read_only_pilot";
}

function matchesRoutePattern(pattern: string, pathname: string): boolean {
  const expected = pattern.split("/").filter(Boolean);
  const actual = pathname.split("/").filter(Boolean);
  for (let index = 0; index < expected.length; index += 1) {
    const segment = expected[index];
    if (segment.startsWith("{...") && segment.endsWith("}")) return actual.length >= index;
    if (index >= actual.length) return false;
    if (segment.startsWith("{") && segment.endsWith("}")) continue;
    if (segment !== actual[index]) return false;
  }
  return actual.length === expected.length;
}

export function isBffRouteAllowed(profileValue: string | undefined, method: string, pathname: string): boolean {
  const profile = normalizeFrontendReleaseProfile(profileValue);
  return profileManifest.routes.some(
    (route) =>
      route.method === method.toUpperCase() &&
      route.profiles.includes(profile) &&
      matchesRoutePattern(route.path, pathname),
  );
}

export function isPageRouteAllowed(profileValue: string | undefined, pathname: string): boolean {
  const profile = normalizeFrontendReleaseProfile(profileValue);
  if (profile === "full_development") return true;
  if (profile === "read_only_pilot") return !pathname.startsWith("/mock-");
  return goldenDemoPagePrefixes.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
