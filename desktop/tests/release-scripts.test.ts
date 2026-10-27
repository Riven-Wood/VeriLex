import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { afterEach, describe, expect, test } from "vitest";

import { createUpdaterManifest } from "../../scripts/create-updater-manifest.mjs";
import { readReleaseVersion } from "../../scripts/release-version.mjs";
import { validateReleaseAssets } from "../../scripts/validate-release-assets.mjs";

const repositoryRoot = resolve(process.cwd(), "..");

const temporaryDirectories: string[] = [];

async function createVersionFixture(): Promise<string> {
  const root = await mkdtemp(join(tmpdir(), "verilex-release-version-"));
  temporaryDirectories.push(root);

  await Promise.all([
    mkdir(join(root, "desktop", "src-tauri"), { recursive: true }),
    mkdir(join(root, "runtime"), { recursive: true }),
  ]);
  await Promise.all([
    writeFile(join(root, "package.json"), '{"name":"verilex","version":"0.3.1"}\n'),
    writeFile(
      join(root, "desktop", "package.json"),
      '{"name":"verilex-desktop","version":"0.3.1"}\n',
    ),
    writeFile(
      join(root, "runtime", "pyproject.toml"),
      '[build-system]\nversion = "9.9.9"\n\n[project]\nname = "verilex-runtime"\nversion = "0.3.1"\n\n[tool.example]\nversion = "8.8.8"\n',
    ),
    writeFile(
      join(root, "desktop", "src-tauri", "Cargo.toml"),
      '[workspace.package]\nversion = "7.7.7"\n\n[package]\nname = "verilex-desktop"\nversion = "0.3.1"\n\n[dependencies]\nversion = "6.6.6"\n',
    ),
    writeFile(
      join(root, "desktop", "src-tauri", "tauri.conf.json"),
      '{"productName":"VeriLex","version":"0.3.1"}\n',
    ),
  ]);

  return root;
}

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true })),
  );
});

describe("release version validation", () => {
  test("returns the stable version when the tag and all five scoped files match", async () => {
    const root = await createVersionFixture();

    expect(readReleaseVersion(root, "v0.3.1")).toBe("0.3.1");
  });

  test.each(["0.3.1", "v0.3.1-rc.1", "v0.3.1+build.1", "v01.3.1"])(
    "rejects a non-release or non-stable tag: %s",
    async (tag) => {
      const root = await createVersionFixture();

      if (tag === "0.3.1") {
        expect(() => readReleaseVersion(root, tag)).toThrow("tag must start with v");
      } else {
        expect(() => readReleaseVersion(root, tag)).toThrow("stable SemVer");
      }
    },
  );

  test("rejects a stable tag that differs from the files", async () => {
    const root = await createVersionFixture();

    expect(() => readReleaseVersion(root, "v0.3.2")).toThrow("version mismatch");
  });

  test.each([
    ["package.json", '{"version":"0.3.2"}\n'],
    ["desktop/package.json", '{"version":"0.3.2"}\n'],
    ["runtime/pyproject.toml", '[project]\nversion = "0.3.2"\n'],
    ["desktop/src-tauri/Cargo.toml", '[package]\nversion = "0.3.2"\n'],
    ["desktop/src-tauri/tauri.conf.json", '{"version":"0.3.2"}\n'],
  ])("names the mismatched path %s", async (relativePath, contents) => {
    const root = await createVersionFixture();
    await writeFile(join(root, relativePath), contents);

    expect(() => readReleaseVersion(root, "v0.3.1")).toThrow(relativePath);
  });

  test("rejects unstable versions inside release files", async () => {
    const root = await createVersionFixture();
    const packagePath = join(root, "package.json");
    const packageJson = JSON.parse(await readFile(packagePath, "utf8"));
    packageJson.version = "0.3.1-rc.1";
    await writeFile(packagePath, `${JSON.stringify(packageJson)}\n`);

    expect(() => readReleaseVersion(root, "v0.3.1")).toThrow("package.json");
  });
});

describe("updater manifest creation", () => {
  const validInput = {
    version: "0.3.1",
    notes: "修复更新流程\n保留本地数据",
    pubDate: "2026-08-11T12:00:00Z",
    repository: "Riven-Wood/VeriLex",
    tag: "v0.3.1",
    artifactName: "VeriLex.app.tar.gz",
    signature: "signed-content",
  };

  test("creates the complete deterministic darwin-aarch64 manifest", () => {
    expect(createUpdaterManifest(validInput)).toEqual({
      version: "0.3.1",
      notes: "修复更新流程\n保留本地数据",
      pub_date: "2026-08-11T12:00:00Z",
      platforms: {
        "darwin-aarch64": {
          signature: "signed-content",
          url: "https://github.com/Riven-Wood/VeriLex/releases/download/v0.3.1/VeriLex.app.tar.gz",
        },
      },
    });
  });

  test.each([
    ["empty notes", { notes: " \n" }],
    ["non-RFC3339 date", { pubDate: "August 11, 2026" }],
    ["impossible RFC3339 calendar date", { pubDate: "2026-02-30T12:00:00Z" }],
    ["empty signature", { signature: "" }],
    ["unstable version", { version: "0.3.1-rc.1", tag: "v0.3.1-rc.1" }],
    ["different repository", { repository: "Riven-Wood/VeriLex-fork" }],
    ["tag not fixed to the version", { tag: "v0.3.2" }],
    ["unsafe tag characters", { tag: "v0.3.1/../../latest" }],
    ["different artifact", { artifactName: "VeriLex_0.3.1_aarch64.dmg" }],
  ])("rejects %s", (_name, override) => {
    expect(() => createUpdaterManifest({ ...validInput, ...override })).toThrow();
  });

  test("CLI writes newline-terminated JSON without printing signature content", async () => {
    const root = await mkdtemp(join(tmpdir(), "verilex-manifest-cli-"));
    temporaryDirectories.push(root);
    const notesPath = join(root, "notes.md");
    const signaturePath = join(root, "VeriLex.app.tar.gz.sig");
    const outputPath = join(root, "latest.json");
    await writeFile(notesPath, "修复更新流程\n保留本地数据");
    await writeFile(signaturePath, "fixture-secret-signature");

    const result = spawnSync(
      process.execPath,
      [
        join(repositoryRoot, "scripts", "create-updater-manifest.mjs"),
        "--version",
        "0.3.1",
        "--notes-file",
        notesPath,
        "--pub-date",
        "2026-08-11T12:00:00Z",
        "--signature-file",
        signaturePath,
        "--output",
        outputPath,
      ],
      { encoding: "utf8" },
    );

    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout).toBe("");
    expect(result.stderr).toBe("");
    const output = await readFile(outputPath, "utf8");
    expect(output.endsWith("\n")).toBe(true);
    expect(JSON.parse(output)).toEqual({
      version: "0.3.1",
      notes: "修复更新流程\n保留本地数据",
      pub_date: "2026-08-11T12:00:00Z",
      platforms: {
        "darwin-aarch64": {
          signature: "fixture-secret-signature",
          url: "https://github.com/Riven-Wood/VeriLex/releases/download/v0.3.1/VeriLex.app.tar.gz",
        },
      },
    });
  });
});

describe("release asset validation", () => {
  const exactAssets = ["latest.json", "VeriLex.app.tar.gz", "VeriLex.app.tar.gz.sig"];

  test("accepts exactly the three required assets in any order", () => {
    expect(() => validateReleaseAssets([...exactAssets].reverse())).not.toThrow();
  });

  test.each([
    ["a missing asset", ["latest.json", "VeriLex.app.tar.gz"]],
    ["a duplicate asset", [...exactAssets, "latest.json"]],
    ["an extra asset", [...exactAssets, "VeriLex.dmg"]],
  ])("rejects %s", (_name, assets) => {
    expect(() => validateReleaseAssets(assets)).toThrow();
  });

  test("CLI validates the real gh release assets JSON shape", () => {
    const result = spawnSync(
      process.execPath,
      [join(repositoryRoot, "scripts", "validate-release-assets.mjs")],
      {
        input: JSON.stringify({ assets: exactAssets.map((name) => ({ name })) }),
        encoding: "utf8",
      },
    );

    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout).toBe("release assets valid\n");
    expect(result.stderr).toBe("");
  });
});
