import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";

const HTTP_METHODS = new Set(["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]);
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(SCRIPT_DIR, "..");
const PROJECT_ROOT = path.resolve(FRONTEND_ROOT, "..");
const BFF_ROOT = path.join(FRONTEND_ROOT, "app", "api");
const BACKEND_INVENTORY_PATH = path.join(PROJECT_ROOT, "docs", "evidence", "routes", "route-inventory-v1.json");
const OUTPUT_PATH = path.join(PROJECT_ROOT, "docs", "evidence", "routes", "bff-route-inventory-v1.json");
const PROFILE_MANIFEST_PATH = path.join(FRONTEND_ROOT, "generated", "bff-profile-manifest.json");

function walkRouteFiles(directory) {
  const files = [];
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...walkRouteFiles(target));
    else if (entry.isFile() && entry.name === "route.ts") files.push(target);
  }
  return files.sort();
}

function hasExportModifier(node) {
  return Boolean(node.modifiers?.some((modifier) => modifier.kind === ts.SyntaxKind.ExportKeyword));
}

function routePathFromFile(file) {
  const relative = path.relative(BFF_ROOT, path.dirname(file)).split(path.sep);
  const segments = relative.map((segment) => {
    const match = /^\[([^\]]+)\]$/.exec(segment);
    return match ? `{${match[1]}}` : segment;
  });
  return `/api/${segments.join("/")}`.replace(/\/$/, "");
}

function normalizePath(value) {
  return value.split("?", 1)[0].replace(/\{[^}]+\}/g, "{param}");
}

function expressionPlaceholder(expression) {
  if (ts.isCallExpression(expression) && expression.expression.getText() === "encodeURIComponent") {
    const argument = expression.arguments[0];
    if (argument && ts.isIdentifier(argument)) return `{${argument.text}}`;
  }
  if (ts.isIdentifier(expression) && !["backend", "query", "search", "searchParams"].includes(expression.text)) {
    return `{${expression.text}}`;
  }
  return "";
}

function pathFromExpression(expression, sourceFile) {
  if (ts.isStringLiteralLike(expression)) return expression.text.split("?", 1)[0];
  if (ts.isTemplateExpression(expression)) {
    let candidate = expression.head.text;
    for (const span of expression.templateSpans) {
      candidate += expressionPlaceholder(span.expression);
      candidate += span.literal.text;
    }
    const apiStart = candidate.indexOf("/api/");
    if (apiStart < 0) return null;
    return candidate.slice(apiStart).split("?", 1)[0];
  }
  if (ts.isBinaryExpression(expression) && expression.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    return pathFromExpression(expression.left, sourceFile) ?? pathFromExpression(expression.right, sourceFile);
  }
  const literal = expression.getText(sourceFile).match(/["'`](\/api\/[^"'`?+]*)/);
  return literal?.[1] ?? null;
}

function explicitBackendMethod(call, fallback, sourceFile) {
  const options = call.arguments[1];
  if (!options || !ts.isObjectLiteralExpression(options)) return fallback;
  for (const property of options.properties) {
    if (!ts.isPropertyAssignment(property) || property.name.getText(sourceFile) !== "method") continue;
    if (ts.isStringLiteralLike(property.initializer)) return property.initializer.text.toUpperCase();
  }
  return fallback;
}

function findBackendCall(node, sourceFile) {
  let found = null;
  function visit(current) {
    if (found) return;
    if (ts.isCallExpression(current)) {
      const callee = current.expression.getText(sourceFile);
      if (callee === "backendFetch" && current.arguments[0]) {
        found = { call: current, path: pathFromExpression(current.arguments[0], sourceFile), auth: "oidc_bearer" };
        return;
      }
      if (callee === "fetch" && current.arguments[0]) {
        const backendPath = pathFromExpression(current.arguments[0], sourceFile);
        if (backendPath?.startsWith("/api/")) {
          found = {
            call: current,
            path: backendPath,
            auth: sourceFile.text.includes("backendAuthHeaders") ? "oidc_bearer" : "missing",
          };
          return;
        }
      }
    }
    ts.forEachChild(current, visit);
  }
  visit(node);
  return found;
}

function exportedHandlers(sourceFile) {
  const handlers = [];
  for (const statement of sourceFile.statements) {
    if (ts.isFunctionDeclaration(statement) && hasExportModifier(statement) && statement.name) {
      const method = statement.name.text.toUpperCase();
      if (HTTP_METHODS.has(method)) handlers.push({ method, node: statement });
      continue;
    }
    if (!ts.isVariableStatement(statement) || !hasExportModifier(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (ts.isObjectBindingPattern(declaration.name)) {
        for (const element of declaration.name.elements) {
          const method = element.name.getText(sourceFile).toUpperCase();
          if (HTTP_METHODS.has(method)) handlers.push({ method, node: declaration });
        }
      } else if (ts.isIdentifier(declaration.name)) {
        const method = declaration.name.text.toUpperCase();
        if (HTTP_METHODS.has(method)) handlers.push({ method, node: declaration.initializer ?? declaration });
      }
    }
  }
  return handlers;
}

function buildPayload() {
  const backendPayload = JSON.parse(fs.readFileSync(BACKEND_INVENTORY_PATH, "utf8"));
  const backendByKey = new Map();
  for (const route of backendPayload.routes) {
    backendByKey.set(`${route.method} ${normalizePath(route.path)}`, route);
  }

  const routeFiles = walkRouteFiles(BFF_ROOT);
  const routes = [];
  const failures = [];
  for (const file of routeFiles) {
    const sourceText = fs.readFileSync(file, "utf8");
    const sourceFile = ts.createSourceFile(file, sourceText, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    const frontendPath = routePathFromFile(file);
    const relativeFile = path.relative(PROJECT_ROOT, file).split(path.sep).join("/");
    const handlers = exportedHandlers(sourceFile);
    if (handlers.length === 0) failures.push(`${relativeFile}: no exported HTTP handlers`);

    for (const handler of handlers) {
      if (frontendPath === "/api/auth/{...nextauth}") {
        routes.push({
          file: relativeFile,
          frontend_method: handler.method,
          frontend_path: frontendPath,
          kind: "nextauth",
          backend_method: null,
          backend_path: null,
          auth_propagation: "nextauth_session",
          tenant_propagation: "session_claim",
          profiles: ["full_development", "golden_demo", "read_only_pilot"],
        });
        continue;
      }
      const backendCall = findBackendCall(handler.node, sourceFile);
      if (!backendCall?.path) {
        failures.push(`${relativeFile}:${handler.method}: backend target could not be parsed`);
        continue;
      }
      const backendMethod = explicitBackendMethod(backendCall.call, handler.method, sourceFile);
      const backendKey = `${backendMethod} ${normalizePath(backendCall.path)}`;
      const backendRoute = backendByKey.get(backendKey);
      if (!backendRoute) {
        failures.push(`${relativeFile}:${handler.method}: unregistered backend target ${backendMethod} ${backendCall.path}`);
        continue;
      }
      if (backendCall.auth !== "oidc_bearer") {
        failures.push(`${relativeFile}:${handler.method}: backend proxy does not propagate OIDC bearer auth`);
        continue;
      }
      routes.push({
        file: relativeFile,
        frontend_method: handler.method,
        frontend_path: frontendPath,
        kind: "backend_proxy",
        backend_method: backendMethod,
        backend_path: backendCall.path,
        backend_group: backendRoute.group,
        auth_propagation: "oidc_bearer",
        tenant_propagation: "backend_token_claim",
        profiles: backendRoute.profiles,
      });
    }
  }
  if (failures.length > 0) throw new Error(`BFF inventory generation failed:\n- ${failures.join("\n- ")}`);
  routes.sort((left, right) =>
    [left.frontend_path, left.frontend_method].join(" ").localeCompare([right.frontend_path, right.frontend_method].join(" ")),
  );
  return {
    schema_version: "1.0",
    generator: "frontend/scripts/generate-bff-route-inventory.mjs",
    route_file_count: routeFiles.length,
    route_count: routes.length,
    routes,
  };
}

const payload = buildPayload();
const profileManifest = {
  schema_version: "1.0",
  generator: "frontend/scripts/generate-bff-route-inventory.mjs",
  routes: payload.routes.map((route) => ({
    method: route.frontend_method,
    path: route.frontend_path,
    profiles: route.profiles,
  })),
};
const expected = `${JSON.stringify(payload, null, 2)}\n`;
const expectedProfileManifest = `${JSON.stringify(profileManifest, null, 2)}\n`;
if (process.argv.includes("--check")) {
  const inventoryCurrent = fs.existsSync(OUTPUT_PATH) && fs.readFileSync(OUTPUT_PATH, "utf8") === expected;
  const manifestCurrent =
    fs.existsSync(PROFILE_MANIFEST_PATH) &&
    fs.readFileSync(PROFILE_MANIFEST_PATH, "utf8") === expectedProfileManifest;
  if (!inventoryCurrent || !manifestCurrent) {
    console.error(`BFF route inventory is stale: ${OUTPUT_PATH}`);
    process.exit(1);
  }
  console.log(`BFF route inventory is current: ${OUTPUT_PATH}`);
} else {
  fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
  fs.writeFileSync(OUTPUT_PATH, expected, "utf8");
  fs.mkdirSync(path.dirname(PROFILE_MANIFEST_PATH), { recursive: true });
  fs.writeFileSync(PROFILE_MANIFEST_PATH, expectedProfileManifest, "utf8");
  console.log(`Wrote ${OUTPUT_PATH}`);
}
