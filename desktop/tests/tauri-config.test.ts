import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

interface TauriConfig {
  build: { beforeBuildCommand?: string };
  bundle: {
    createUpdaterArtifacts?: boolean;
    resources?: Record<string, string>;
  };
  plugins?: {
    updater?: {
      endpoints?: string[];
      pubkey?: string;
      dangerousInsecureTransportProtocol?: boolean;
    };
  };
}

describe("Tauri production bundle", () => {
  it("builds and embeds the local Runtime sidecar", async () => {
    const raw = await readFile(
      resolve(process.cwd(), "src-tauri/tauri.conf.json"),
      "utf8",
    );
    const config = JSON.parse(raw) as TauriConfig;

    expect(config.build.beforeBuildCommand).toContain("build:sidecar");
    expect(config.bundle.resources).toMatchObject({
      sidecar: "sidecar",
      "../../LICENSE": "licenses/VERILEX_LICENSE.txt",
      "../../THIRD_PARTY_NOTICES.md": "licenses/THIRD_PARTY_NOTICES.md",
    });
  });

  it("requires signed HTTPS updater artifacts and the minimum native permissions", async () => {
    const [configRaw, capabilitiesRaw, cargoToml, rustSource] =
      await Promise.all([
        readFile(resolve(process.cwd(), "src-tauri/tauri.conf.json"), "utf8"),
        readFile(
          resolve(process.cwd(), "src-tauri/capabilities/default.json"),
          "utf8",
        ),
        readFile(resolve(process.cwd(), "src-tauri/Cargo.toml"), "utf8"),
        readFile(resolve(process.cwd(), "src-tauri/src/lib.rs"), "utf8"),
      ]);
    const config = JSON.parse(configRaw) as TauriConfig;
    const capabilities = JSON.parse(capabilitiesRaw) as {
      permissions: string[];
    };

    expect(config.bundle.createUpdaterArtifacts).toBe(true);
    expect(config.plugins?.updater?.endpoints).toEqual([
      "https://github.com/Riven-Wood/VeriLex/releases/latest/download/latest.json",
    ]);
    expect(config.plugins?.updater?.pubkey).toMatch(/\S{40,}/);
    expect(config.plugins?.updater).not.toHaveProperty(
      "dangerousInsecureTransportProtocol",
    );
    expect(capabilities.permissions).toEqual(
      expect.arrayContaining([
        "updater:default",
        "process:allow-restart",
      ]),
    );
    expect(capabilities.permissions).not.toContain("process:default");
    expect(capabilities.permissions).not.toEqual(expect.arrayContaining([
      "fs:default",
      "fs:allow-read-file",
      "fs:allow-stat",
      "fs:allow-write-file",
      "fs:allow-remove",
    ]));

    expect(cargoToml).toMatch(/^rust-version = "1\.88"$/m);
    expect(cargoToml).toContain("tauri-plugin-updater");
    expect(cargoToml).toContain("tauri-plugin-process");
    expect(cargoToml).not.toContain("tauri-plugin-fs");
    expect(rustSource).toContain("tauri_plugin_updater::Builder::new().build()");
    expect(rustSource).toContain("tauri_plugin_process::init()");
    expect(rustSource).toContain("read_dropped_file");
    expect(rustSource).toContain("DroppedFileGrants");
  });
});
