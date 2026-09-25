import { build } from "esbuild";

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const tenantId = (process.env.WORKSHOP_AZURE_TENANT_ID || "").trim();
const clientId = (process.env.WORKSHOP_AZURE_CLIENT_ID || "").trim();
const redirectUri = (
  process.env.WORKSHOP_AZURE_REDIRECT_URI ||
  "https://charliekw411.github.io/sre-agent-workshop/"
).trim();
const requireConfig = process.env.WORKSHOP_REQUIRE_AZURE_CONFIG === "true";

if ((tenantId && !clientId) || (!tenantId && clientId)) {
  throw new Error(
    "WORKSHOP_AZURE_TENANT_ID and WORKSHOP_AZURE_CLIENT_ID must be configured together.",
  );
}
for (const [name, value] of [
  ["WORKSHOP_AZURE_TENANT_ID", tenantId],
  ["WORKSHOP_AZURE_CLIENT_ID", clientId],
]) {
  if (value && !UUID.test(value)) {
    throw new Error(`${name} must be a UUID.`);
  }
}
if (requireConfig && (!tenantId || !clientId)) {
  throw new Error(
    "Azure site authentication is required, but WORKSHOP_AZURE_TENANT_ID or " +
      "WORKSHOP_AZURE_CLIENT_ID is missing.",
  );
}

const redirect = new URL(redirectUri);
if (redirect.protocol !== "https:" && redirect.hostname !== "localhost") {
  throw new Error("WORKSHOP_AZURE_REDIRECT_URI must use HTTPS unless it targets localhost.");
}
if (redirect.search || redirect.hash) {
  throw new Error("WORKSHOP_AZURE_REDIRECT_URI cannot contain a query string or fragment.");
}

const workshopConfig = Object.freeze({
  tenantId,
  clientId,
  redirectUri: redirect.href,
  resourceGroupPrefix: "rg-sre-agent-workshop-",
  environmentTagName: "workshop-architecture",
  environmentTagValue: "single-vm",
  refreshSeconds: 60,
});

await build({
  entryPoints: ["web/src/index.js"],
  outfile: "docs/assets/javascripts/workshop.js",
  bundle: true,
  minify: true,
  sourcemap: false,
  target: ["es2020"],
  legalComments: "eof",
  define: {
    __WORKSHOP_CONFIG__: JSON.stringify(workshopConfig),
  },
  logLevel: "info",
});
