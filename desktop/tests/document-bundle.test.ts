import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { promisify } from "node:util";

import { describe, expect, it } from "vitest";

const execFileAsync = promisify(execFile);

describe("macOS Documents production bundle", () => {
  it("keeps Documents resources out of the base and Windows package", async () => {
    const base = JSON.parse(
      await readFile(resolve(process.cwd(), "src-tauri/tauri.conf.json"), "utf8"),
    ) as { bundle: { resources: Record<string, string> } };
    const overlay = JSON.parse(
      await readFile(
        resolve(process.cwd(), "src-tauri/tauri.macos-documents.conf.json"),
        "utf8",
      ),
    ) as { bundle: { resources: Record<string, string> } };

    expect(JSON.stringify(base)).not.toContain("generated/documents");
    expect(base.bundle.resources).not.toHaveProperty("sidecar/documents");
    expect(overlay.bundle.resources).toEqual({
      "generated/documents-macos-arm64": "sidecar/documents",
    });
  });

  it("locks a local-only renderer compatible with the production CSP", async () => {
    const [lockRaw, baseConfigRaw, overlayConfigRaw] = await Promise.all([
      readFile(resolve(process.cwd(), "src/documents/renderer-lock.json"), "utf8"),
      readFile(resolve(process.cwd(), "src-tauri/tauri.conf.json"), "utf8"),
      readFile(
        resolve(process.cwd(), "src-tauri/tauri.macos-documents.conf.json"),
        "utf8",
      ),
    ]);
    const lock = JSON.parse(lockRaw) as {
      route: string;
      assets: { path: string; sha256: string }[];
      asset_file_count: number;
      asset_tree_sha256: string;
      required_csp_features: string[];
    };
    const baseCsp = (
      JSON.parse(baseConfigRaw) as { app: { security: { csp: string } } }
    ).app.security.csp;
    const documentCsp = (
      JSON.parse(overlayConfigRaw) as { app: { security: { csp: string } } }
    ).app.security.csp;

    expect(lock.route).toBe("docxodus");
    expect(lock.assets.length).toBeGreaterThan(0);
    expect(lock.assets.every((asset) => !/^https?:/i.test(asset.path))).toBe(true);
    expect(lock.assets.every((asset) => /^[0-9a-f]{64}$/.test(asset.sha256))).toBe(true);
    expect(lock.asset_file_count).toBeGreaterThanOrEqual(lock.assets.length);
    expect(lock.asset_tree_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(lock.required_csp_features).toEqual(["wasm", "worker-self", "style-blob"]);
    expect(baseCsp).toContain("wasm-unsafe-eval");
    // Contract review's read-only DOCX preview also uses local Blob styles.
    expect(baseCsp).toContain("style-src 'self' blob:");
    expect(baseCsp).not.toContain("'unsafe-inline'");
    expect(baseCsp).toContain("connect-src 'self' blob:");
    expect(documentCsp).toContain("script-src 'self' 'wasm-unsafe-eval'");
    expect(documentCsp).toContain("style-src 'self' blob:");
    expect(documentCsp).toContain("connect-src 'self' blob:");
    expect(documentCsp).not.toContain("'unsafe-inline'");
    expect(documentCsp).not.toContain("'unsafe-eval'");
  });

  it("exposes a deterministic offline build contract", async () => {
    const { stdout } = await execFileAsync(process.execPath, [
      resolve(process.cwd(), "scripts/build-document-bundle.mjs"),
      "--check-contract",
    ]);

    expect(JSON.parse(stdout)).toEqual({
      schema_version: 1,
      platform: "macos",
      arch: "arm64",
      network_during_build: "denied",
      output: "src-tauri/generated/documents-macos-arm64",
      manifest: "component-manifest.json",
    });
  });

  it("wires the document build through desktop and repository package scripts", async () => {
    const [desktopPackage, rootPackage, sidecarBuild] = await Promise.all([
      readFile(resolve(process.cwd(), "package.json"), "utf8"),
      readFile(resolve(process.cwd(), "../package.json"), "utf8"),
      readFile(resolve(process.cwd(), "scripts/build-runtime-sidecar.mjs"), "utf8"),
    ]);

    expect(JSON.parse(desktopPackage).scripts["build:documents"]).toBe(
      "node scripts/build-document-bundle.mjs",
    );
    expect(JSON.parse(rootPackage).scripts["build:documents"]).toBe(
      "npm --workspace desktop run build:documents",
    );
    expect(sidecarBuild).toContain("const overlaySelected =");
    expect(sidecarBuild).toContain("if (overlaySelected)");
    expect(sidecarBuild).not.toContain("if (existsSync(documentManifest))");
  });
});
