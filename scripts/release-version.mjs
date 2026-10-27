import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const STABLE_SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;

function readJsonVersion(repositoryRoot, relativePath) {
  try {
    const value = JSON.parse(readFileSync(resolve(repositoryRoot, relativePath), "utf8")).version;
    if (typeof value !== "string" || !STABLE_SEMVER.test(value)) {
      throw new Error("version must be stable SemVer");
    }
    return value;
  } catch (error) {
    throw new Error(`${relativePath}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function readTomlVersion(repositoryRoot, relativePath, sectionName) {
  try {
    const contents = readFileSync(resolve(repositoryRoot, relativePath), "utf8");
    let currentSection = "";
    const versions = [];

    for (const line of contents.split(/\r?\n/)) {
      const section = line.match(/^\s*\[([^\]]+)]\s*(?:#.*)?$/);
      if (section) {
        currentSection = section[1];
        continue;
      }
      if (currentSection !== sectionName) continue;

      const version = line.match(/^\s*version\s*=\s*"([^"]+)"\s*(?:#.*)?$/);
      if (version) versions.push(version[1]);
    }

    if (versions.length !== 1 || !STABLE_SEMVER.test(versions[0])) {
      throw new Error(`[${sectionName}] version must be a single stable SemVer`);
    }
    return versions[0];
  } catch (error) {
    throw new Error(`${relativePath}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

export function readReleaseVersion(repositoryRoot, tag) {
  if (typeof tag !== "string" || !tag.startsWith("v")) {
    throw new Error("tag must start with v");
  }

  const tagVersion = tag.slice(1);
  if (!STABLE_SEMVER.test(tagVersion)) {
    throw new Error("tag version must be stable SemVer");
  }

  const versions = [
    ["package.json", readJsonVersion(repositoryRoot, "package.json")],
    ["desktop/package.json", readJsonVersion(repositoryRoot, "desktop/package.json")],
    [
      "runtime/pyproject.toml",
      readTomlVersion(repositoryRoot, "runtime/pyproject.toml", "project"),
    ],
    [
      "desktop/src-tauri/Cargo.toml",
      readTomlVersion(repositoryRoot, "desktop/src-tauri/Cargo.toml", "package"),
    ],
    [
      "desktop/src-tauri/tauri.conf.json",
      readJsonVersion(repositoryRoot, "desktop/src-tauri/tauri.conf.json"),
    ],
  ];

  for (const [relativePath, version] of versions) {
    if (version !== tagVersion) {
      throw new Error(`${relativePath}: version mismatch (expected ${tagVersion}, found ${version})`);
    }
  }

  return tagVersion;
}

function runCli() {
  const arguments_ = process.argv.slice(2);
  if (arguments_.length > 1) throw new Error("expected a single tag argument");
  const tag = arguments_[0] ?? process.env.GITHUB_REF_NAME;
  const version = readReleaseVersion(process.cwd(), tag);
  process.stdout.write(`${version}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    runCli();
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
