import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const REQUIRED_ASSETS = ["VeriLex.app.tar.gz", "VeriLex.app.tar.gz.sig", "latest.json"];

export function validateReleaseAssets(assetNames) {
  if (!Array.isArray(assetNames) || assetNames.some((name) => typeof name !== "string")) {
    throw new Error("asset names must be an array of strings");
  }

  const actual = [...assetNames].sort();
  const expected = [...REQUIRED_ASSETS].sort();
  if (actual.length !== expected.length || actual.some((name, index) => name !== expected[index])) {
    throw new Error(`release assets must be exactly: ${REQUIRED_ASSETS.join(", ")}`);
  }
}

function runCli() {
  const payload = JSON.parse(readFileSync(0, "utf8"));
  if (!payload || !Array.isArray(payload.assets)) {
    throw new Error("stdin must contain gh release JSON with an assets array");
  }
  const assetNames = payload.assets.map((asset) => {
    if (!asset || typeof asset !== "object" || typeof asset.name !== "string") {
      throw new Error("each release asset must have a string name");
    }
    return asset.name;
  });
  validateReleaseAssets(assetNames);
  process.stdout.write("release assets valid\n");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    runCli();
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
