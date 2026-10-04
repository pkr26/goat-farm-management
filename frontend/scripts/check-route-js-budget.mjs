import { readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const BUILD_DIR = join(process.cwd(), ".next");
const ROUTES = [
  {
    route: "/login",
    manifest: "server/app/login/page_client-reference-manifest.js",
    maxGzipBytes: 455 * 1024,
  },
  {
    route: "/simulation",
    manifest: "server/app/(app)/simulation/page_client-reference-manifest.js",
    maxGzipBytes: 535 * 1024,
  },
];

function readClientManifest(relativePath) {
  const source = readFileSync(join(BUILD_DIR, relativePath), "utf8");
  const assignment = source.lastIndexOf(" = ");
  if (assignment < 0) throw new Error(`Could not parse ${relativePath}`);
  return JSON.parse(source.slice(assignment + 3).replace(/;\s*$/, ""));
}

function initialChunks(manifest) {
  const buildManifest = JSON.parse(
    readFileSync(join(BUILD_DIR, "build-manifest.json"), "utf8"),
  );
  const chunks = new Set([
    ...buildManifest.polyfillFiles,
    ...buildManifest.rootMainFiles,
  ]);
  for (const clientModule of Object.values(manifest.clientModules)) {
    if (clientModule.async) continue;
    for (const chunk of clientModule.chunks ?? []) chunks.add(chunk);
  }
  return [...chunks]
    .map((chunk) => chunk.replace(/^\/_next\//, ""))
    .filter((chunk) => chunk.endsWith(".js"));
}

let failed = false;
for (const budget of ROUTES) {
  const manifest = readClientManifest(budget.manifest);
  const chunks = initialChunks(manifest);
  const rawBytes = chunks.reduce(
    (total, chunk) => total + statSync(join(BUILD_DIR, chunk)).size,
    0,
  );
  const gzipBytes = chunks.reduce(
    (total, chunk) =>
      total + gzipSync(readFileSync(join(BUILD_DIR, chunk)), { level: 6 }).length,
    0,
  );
  const teluguCatalogShippedCold = chunks.some((chunk) =>
    readFileSync(join(BUILD_DIR, chunk), "utf8").includes('"tasks.title":"పనులు"'),
  );
  const maximumKiB = Math.ceil(budget.maxGzipBytes / 1024);
  const measuredKiB = Math.ceil(gzipBytes / 1024);
  console.log(
    `${budget.route}: ${chunks.length} initial scripts, ${rawBytes} raw bytes, ` +
      `${gzipBytes} gzip bytes (${measuredKiB} KiB; budget ${maximumKiB} KiB)`,
  );
  if (gzipBytes > budget.maxGzipBytes) {
    failed = true;
    console.error(`${budget.route} exceeds its cold-route JavaScript budget`);
  }
  if (teluguCatalogShippedCold) {
    failed = true;
    console.error(`${budget.route} eagerly ships the Telugu locale catalog`);
  }
}

if (failed) process.exitCode = 1;
