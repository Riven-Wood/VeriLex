import { spawnSync } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, test } from "vitest";

const repositoryRoot = resolve(process.cwd(), "..");
const workflowPath = join(repositoryRoot, ".github", "workflows", "release-macos-arm64.yml");
const workflowSource = await readFile(workflowPath, "utf8").catch(() => "");
const runtimeLockSource = await readFile(join(repositoryRoot, "runtime", "uv.lock"), "utf8").catch(
  () => "",
);

type ParsedLine = { indent: number; text: string };
type NodeRange = { indent: number; start: number; end: number };

const parsedLines: ParsedLine[] = workflowSource.split(/\r?\n/).map((line) => ({
  indent: line.match(/^ */)?.[0].length ?? 0,
  text: line.trim(),
}));

function mappingNode(path: string[]): NodeRange {
  let range: NodeRange = { indent: -2, start: -1, end: parsedLines.length };

  for (const key of path) {
    const childIndent = range.indent + 2;
    const start = parsedLines.findIndex(
      (line, index) =>
        index > range.start &&
        index < range.end &&
        line.indent === childIndent &&
        line.text === `${key}:`,
    );
    if (start === -1) throw new Error(`missing workflow mapping: ${path.join(".")}`);

    let end = range.end;
    for (let index = start + 1; index < range.end; index += 1) {
      if (parsedLines[index].text !== "" && parsedLines[index].indent <= childIndent) {
        end = index;
        break;
      }
    }
    range = { indent: childIndent, start, end };
  }

  return range;
}

function mappingKeys(path: string[]): string[] {
  const range = mappingNode(path);
  const childIndent = range.indent + 2;
  return parsedLines
    .slice(range.start + 1, range.end)
    .filter((line) => line.indent === childIndent && /^[^:#]+:/.test(line.text))
    .map((line) => line.text.slice(0, line.text.indexOf(":")));
}

function scalar(path: string[]): string {
  const parent = mappingNode(path.slice(0, -1));
  const key = path.at(-1);
  const prefix = `${key}:`;
  const line = parsedLines.find(
    (candidate, index) =>
      index > parent.start &&
      index < parent.end &&
      candidate.indent === parent.indent + 2 &&
      candidate.text.startsWith(prefix),
  );
  if (!line) throw new Error(`missing workflow scalar: ${path.join(".")}`);
  return line.text
    .slice(prefix.length)
    .replace(/\s+#.*$/, "")
    .trim()
    .replace(/^(.*)$/, "$1")
    .replace(/^(["'])(.*)\1$/, "$2");
}

function sequence(path: string[]): string[] {
  const range = mappingNode(path);
  return parsedLines
    .slice(range.start + 1, range.end)
    .filter((line) => line.indent === range.indent + 2 && line.text.startsWith("- "))
    .map((line) => line.text.slice(2).replace(/^(["'])(.*)\1$/, "$2"));
}

type WorkflowStep = {
  name: string;
  uses?: string;
  run?: string;
  env: Record<string, string>;
  with: Record<string, string>;
};

function parseSteps(jobName = "build"): WorkflowStep[] {
  const range = mappingNode(["jobs", jobName, "steps"]);
  const stepIndent = range.indent + 2;
  const starts = parsedLines
    .map((line, index) => ({ line, index }))
    .filter(
      ({ line, index }) =>
        index > range.start &&
        index < range.end &&
        line.indent === stepIndent &&
        line.text.startsWith("- name:"),
    )
    .map(({ index }) => index);

  return starts.map((start, stepIndex) => {
    const end = starts[stepIndex + 1] ?? range.end;
    const name = parsedLines[start].text.slice("- name:".length).trim();
    const attributes = parsedLines.slice(start + 1, end);

    function attributeValue(key: string): string | undefined {
      const prefix = `${key}:`;
      return attributes
        .find((line) => line.indent === stepIndent + 2 && line.text.startsWith(prefix))
        ?.text.slice(prefix.length)
        .replace(/\s+#.*$/, "")
        .trim();
    }

    function explicitChildMap(key: "env" | "with"): Record<string, string> {
      const nodeIndex = attributes.findIndex(
        (line) => line.indent === stepIndent + 2 && line.text === `${key}:`,
      );
      if (nodeIndex === -1) return {};
      const result: Record<string, string> = {};
      for (let index = nodeIndex + 1; index < attributes.length; index += 1) {
        const line = attributes[index];
        if (line.text !== "" && line.indent <= stepIndent + 2) break;
        if (line.indent !== stepIndent + 4) continue;
        const separator = line.text.indexOf(":");
        result[line.text.slice(0, separator)] = line.text.slice(separator + 1).trim();
      }
      return result;
    }

    const runIndex = attributes.findIndex(
      (line) => line.indent === stepIndent + 2 && /^run:\s*[|>]/.test(line.text),
    );
    const inlineRun = attributeValue("run");
    let run = inlineRun && !/^[|>]/.test(inlineRun) ? inlineRun : undefined;
    if (runIndex !== -1) {
      const commandLines: string[] = [];
      for (let index = runIndex + 1; index < attributes.length; index += 1) {
        const line = attributes[index];
        if (line.text !== "" && line.indent <= stepIndent + 2) break;
        commandLines.push(" ".repeat(Math.max(0, line.indent - stepIndent - 4)) + line.text);
      }
      run = commandLines.join("\n").trimEnd();
    }

    return {
      name,
      uses: attributeValue("uses"),
      run,
      env: explicitChildMap("env"),
      with: explicitChildMap("with"),
    };
  });
}

function shellStep(steps: WorkflowStep[], name: string): string {
  const command = steps.find((step) => step.name === name)?.run;
  if (!command) throw new Error(`missing shell step: ${name}`);
  return command;
}

const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories.splice(0).map((directory) => rm(directory, { recursive: true })),
  );
});

describe("macOS release workflow contract", () => {
  test("ordinary pushes and pull requests cannot enter the release job", () => {
    expect(workflowSource).not.toBe("");
    expect(mappingKeys(["on"])).toEqual(["push"]);
    expect(mappingKeys(["on", "push"])).toEqual(["tags"]);
    expect(sequence(["on", "push", "tags"])).toEqual(["v*"]);
  });

  test("grants only release-content permission and serializes each tag without cancellation", () => {
    expect(mappingKeys(["jobs"])).toEqual(["build", "sign", "publish"]);
    for (const jobName of ["build", "sign", "publish"]) {
      expect(mappingKeys(["jobs", jobName, "permissions"])).toEqual(["contents"]);
    }
    expect(scalar(["jobs", "build", "permissions", "contents"])).toBe("read");
    expect(scalar(["jobs", "sign", "permissions", "contents"])).toBe("read");
    expect(scalar(["jobs", "publish", "permissions", "contents"])).toBe("write");
    expect(scalar(["jobs", "sign", "needs"])).toBe("build");
    expect(scalar(["jobs", "publish", "needs"])).toBe("sign");
    expect(scalar(["concurrency", "group"])).toBe("release-${{ github.ref_name }}");
    expect(scalar(["concurrency", "cancel-in-progress"])).toBe("false");
    for (const jobName of ["build", "sign", "publish"]) {
      expect(Number(scalar(["jobs", jobName, "timeout-minutes"]))).toBeGreaterThan(0);
      expect(scalar(["jobs", jobName, "runs-on"])).toBe("macos-14");
    }
  });

  test("pins the arm64 build environment and immutable official setup actions", () => {
    const steps = parseSteps();
    expect(scalar(["jobs", "build", "runs-on"])).toBe("macos-14");
    expect(steps.find((step) => step.name === "Checkout")?.uses).toMatch(
      /^actions\/checkout@[0-9a-f]{40}$/,
    );
    expect(steps.find((step) => step.name === "Checkout")?.with["fetch-depth"]).toBe("0");
    expect(steps.find((step) => step.name === "Checkout")?.with["persist-credentials"]).toBe(
      "false",
    );
    expect(steps.find((step) => step.name === "Setup Node")?.uses).toMatch(
      /^actions\/setup-node@[0-9a-f]{40}$/,
    );
    expect(steps.find((step) => step.name === "Setup Node")?.with["node-version"]).toBe("20");
    expect(steps.find((step) => step.name === "Setup Python")?.uses).toMatch(
      /^actions\/setup-python@[0-9a-f]{40}$/,
    );
    expect(steps.find((step) => step.name === "Setup Python")?.with["python-version"]).toBe(
      "3.13.14",
    );
    expect(steps.find((step) => step.name === "Setup uv")?.uses).toMatch(
      /^astral-sh\/setup-uv@[0-9a-f]{40}$/,
    );
    expect(shellStep(steps, "Setup Rust")).toContain(
      "rustup toolchain install 1.88.0 --profile minimal --target aarch64-apple-darwin",
    );
    const approvedActions = new Set([
      "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
      "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
      "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
      "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9",
      "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
      "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
    ]);
    const actionUses = ["build", "sign", "publish"]
      .flatMap((jobName) => parseSteps(jobName))
      .map((step) => step.uses)
      .filter((uses): uses is string => uses !== undefined);
    expect(actionUses.length).toBeGreaterThan(0);
    expect(actionUses.every((uses) => approvedActions.has(uses))).toBe(true);
  });

  test("installs the hash-locked runtime and validates all code before the updater build", () => {
    const steps = parseSteps();
    const names = steps.map((step) => step.name);
    const buildIndex = names.indexOf("Build updater archive");
    const requiredBeforeBuild = [
      "Validate annotated tag and release notes",
      "Install dependencies",
      "Validate release version",
      "Test runtime",
      "Test desktop",
      "Build desktop",
      "Check Rust formatting",
      "Test Rust",
      "Check Rust",
    ];

    expect(buildIndex).toBeGreaterThan(-1);
    for (const name of requiredBeforeBuild) {
      expect(names.indexOf(name), name).toBeGreaterThan(-1);
      expect(names.indexOf(name), name).toBeLessThan(buildIndex);
    }
    const install = shellStep(steps, "Install dependencies");
    expect(install).toContain("npm ci");
    expect(install).toContain(
      "uv sync --project runtime --locked --extra dev --python 3.13.14",
    );
    expect(install).not.toMatch(/\bpip(?:3)?\s+install\b|python\s+-m\s+pip\b/);
    expect(runtimeLockSource).not.toBe("");
    expect(runtimeLockSource).toContain('requires-python = ">=3.10, <3.14"');
    expect(runtimeLockSource).toMatch(/sdist = \{[^\n]+hash = "sha256:[0-9a-f]{64}"/);
    expect(runtimeLockSource).toMatch(/wheels = \[[\s\S]+hash = "sha256:[0-9a-f]{64}"/);
    expect(shellStep(steps, "Validate release version")).toContain(
      'npm run --silent check:release-version -- "$GITHUB_REF_NAME"',
    );
    expect(shellStep(steps, "Build updater archive")).toContain(
      "(cd desktop && ../node_modules/.bin/tauri build --target aarch64-apple-darwin --bundles app)",
    );
  });

  test("isolates production signing secrets to a minimal direct signer step", () => {
    const steps = parseSteps("sign");
    const signer = steps.find((step) => step.name === "Sign updater archive");
    expect(signer?.env).toEqual({
      TAURI_SIGNING_PRIVATE_KEY: "${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}",
      TAURI_SIGNING_PRIVATE_KEY_PASSWORD:
        "${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}",
    });
    expect(signer?.run).toContain("node_modules/.bin/tauri signer sign");
    expect(signer?.run).not.toMatch(/\bnpm\b|\bpython\b|\bcargo\b|tauri\s+build/);
    const secretSteps = steps.filter((step) =>
      JSON.stringify(step).includes("secrets.TAURI_SIGNING_PRIVATE_KEY"),
    );
    expect(secretSteps.map((step) => step.name)).toEqual(["Sign updater archive"]);
    const allSteps = ["build", "sign", "publish"].flatMap((jobName) => parseSteps(jobName));
    expect(workflowSource.match(/secrets\.TAURI_SIGNING_PRIVATE_KEY/g)?.length).toBe(2);
    expect(JSON.stringify(parseSteps("build"))).not.toContain("secrets.TAURI_SIGNING");
    expect(JSON.stringify(parseSteps("publish"))).not.toContain("secrets.TAURI_SIGNING");
    for (const step of allSteps) {
      expect(step.run ?? "").not.toMatch(/(?:echo|printf|printenv|set\s+-x).*TAURI_SIGNING/i);
    }
  });

  test("signs on a fresh runner without executing dependency lifecycle or build code", () => {
    const steps = parseSteps("sign");
    expect(steps.map((step) => step.name)).toEqual([
      "Checkout",
      "Setup Node",
      "Install locked signer CLI",
      "Download unsigned archive",
      "Verify unsigned archive",
      "Sign updater archive",
      "Verify signed updater files",
      "Upload signed updater files",
    ]);
    expect(shellStep(steps, "Install locked signer CLI")).toBe("npm ci --ignore-scripts");
    expect(JSON.stringify(steps)).not.toMatch(/uv sync|pytest|build:sidecar|tauri\s+build|cargo/);
    expect(steps.find((step) => step.name === "Download unsigned archive")?.uses).toMatch(
      /^actions\/download-artifact@[0-9a-f]{40}$/,
    );
    expect(steps.find((step) => step.name === "Upload signed updater files")?.uses).toMatch(
      /^actions\/upload-artifact@[0-9a-f]{40}$/,
    );
    expect(steps.find((step) => step.name === "Download unsigned archive")?.with).toMatchObject({
      name: "verilex-macos-arm64-unsigned",
      path: "${{ runner.temp }}/unsigned-updater",
    });
    expect(steps.find((step) => step.name === "Upload signed updater files")?.with).toMatchObject({
      name: "verilex-macos-arm64-signed",
      "if-no-files-found": "error",
      "retention-days": "1",
    });
  });

  test("builds with an ephemeral key and discards its signature before production signing", () => {
    const steps = parseSteps();
    const build = steps.find((step) => step.name === "Build updater archive");
    expect(build?.env).toEqual({});
    expect(build?.run).toContain("signer generate");
    expect(build?.run).toContain("$RUNNER_TEMP/verilex-ephemeral.key");
    expect(build?.run).toContain(
      "(cd desktop && ../node_modules/.bin/tauri build --target aarch64-apple-darwin --bundles app)",
    );
    expect(build?.run).toContain('rm "$ARTIFACT_DIR/VeriLex.app.tar.gz.sig"');
    expect(steps.find((step) => step.name === "Upload unsigned archive")?.uses).toMatch(
      /^actions\/upload-artifact@[0-9a-f]{40}$/,
    );
  });

  test("creates exactly three artifacts in a draft, validates them, then publishes", () => {
    const steps = parseSteps("publish");
    const names = steps.map((step) => step.name);
    expect(names.indexOf("Verify signed updater files")).toBeLessThan(
      names.indexOf("Create updater manifest"),
    );
    expect(names.indexOf("Create updater manifest")).toBeLessThan(
      names.indexOf("Stage draft GitHub Release"),
    );
    expect(names.indexOf("Stage draft GitHub Release")).toBeLessThan(
      names.indexOf("Validate release assets"),
    );
    expect(names.indexOf("Validate release assets")).toBeLessThan(
      names.indexOf("Publish GitHub Release"),
    );

    const verify = shellStep(steps, "Verify signed updater files");
    expect(verify).toContain('test -s "$SIGNED_DIR/VeriLex.app.tar.gz"');
    expect(verify).toContain('test -s "$SIGNED_DIR/VeriLex.app.tar.gz.sig"');
    expect(verify).toContain('find "$SIGNED_DIR" -type f');
    const manifest = shellStep(steps, "Create updater manifest");
    expect(manifest).toContain(
      "git for-each-ref --format='%(taggerdate:iso-strict)' \"refs/tags/$GITHUB_REF_NAME\"",
    );
    expect(manifest).toContain("node scripts/create-updater-manifest.mjs");
    expect(manifest).toContain("--notes-file release-notes.md");
    expect(manifest).toContain(
      '--signature-file "$SIGNED_DIR/VeriLex.app.tar.gz.sig"',
    );
    expect(manifest).toContain("--output latest.json");
    const stage = shellStep(steps, "Stage draft GitHub Release");
    expect(stage).toContain('gh release create "$GITHUB_REF_NAME"');
    expect(stage).toContain("--verify-tag --draft");
    expect(stage).toContain('gh release upload "$GITHUB_REF_NAME"');
    expect(stage).toContain("--clobber");
    expect(stage).not.toMatch(/gh release edit[\s\S]*?--draft/);
    expect(stage.match(/assert_draft/g)?.length).toBeGreaterThanOrEqual(3);
    expect(stage.match(/#(?:VeriLex\.app\.tar\.gz(?:\.sig)?|latest\.json)/g)).toEqual([
      "#VeriLex.app.tar.gz",
      "#VeriLex.app.tar.gz.sig",
      "#latest.json",
    ]);
    expect(shellStep(steps, "Validate release assets")).toContain(
      'gh release view "$GITHUB_REF_NAME" --json assets | node scripts/validate-release-assets.mjs',
    );
    expect(shellStep(steps, "Publish GitHub Release")).toContain(
      'gh release edit "$GITHUB_REF_NAME" --draft=false',
    );
  });

  test.each([
    ["missing,true,true", 0, ["view", "create", "view", "edit", "view", "upload"]],
    ["true,true,true", 0, ["view", "view", "edit", "view", "upload"]],
    ["false", 1, ["view", "view"]],
    ["true,false", 1, ["view", "view"]],
    ["true,true,false", 1, ["view", "view", "edit", "view"]],
  ])("handles release-state sequence %s without mutating a published release", async (states, status, calls) => {
    const root = await mkdtemp(join(tmpdir(), "verilex-release-draft-"));
    temporaryDirectories.push(root);
    const bin = join(root, "bin");
    await mkdir(bin);
    const callLog = join(root, "calls.log");
    const stateIndex = join(root, "state-index");
    const ghPath = join(bin, "gh");
    await writeFile(
      ghPath,
      `#!/bin/bash\nset -eu\nprintf '%s\\n' "$2" >> "$CALL_LOG"\nif [ "$2" = view ]; then\n  index=1\n  if [ -f "$STATE_INDEX" ]; then index=$(($(cat "$STATE_INDEX") + 1)); fi\n  printf '%s' "$index" > "$STATE_INDEX"\n  state=$(printf '%s' "$RELEASE_STATES" | cut -d, -f"$index")\n  if [ "$state" = missing ]; then exit 1; fi\n  printf '%s\\n' "$state"\nfi\n`,
    );
    await chmod(ghPath, 0o755);
    const result = spawnSync(
      "/bin/bash",
      ["-c", `set -euo pipefail\n${shellStep(parseSteps("publish"), "Stage draft GitHub Release")}`],
      {
        cwd: root,
        encoding: "utf8",
        env: {
          ...process.env,
          PATH: `${bin}:${process.env.PATH}`,
          CALL_LOG: callLog,
          RELEASE_STATES: states,
          STATE_INDEX: stateIndex,
          GITHUB_REF_NAME: "v0.3.0",
          RUNNER_TEMP: root,
        },
      },
    );
    expect(result.status, result.stderr).toBe(status);
    expect((await readFile(callLog, "utf8")).trim().split("\n")).toEqual(calls);
  });

  test.each([
    ["true", 0, ["view", "edit"]],
    ["false", 1, ["view"]],
  ])("rechecks draft state %s immediately before publishing", async (state, status, calls) => {
    const root = await mkdtemp(join(tmpdir(), "verilex-release-publish-"));
    temporaryDirectories.push(root);
    const ghPath = join(root, "gh");
    const callLog = join(root, "calls.log");
    await writeFile(
      ghPath,
      `#!/bin/bash\nset -eu\nprintf '%s\\n' "$2" >> "$CALL_LOG"\nif [ "$2" = view ]; then printf '%s\\n' "$RELEASE_STATE"; fi\n`,
    );
    await chmod(ghPath, 0o755);
    const result = spawnSync(
      "/bin/bash",
      ["-c", `set -euo pipefail\n${shellStep(parseSteps("publish"), "Publish GitHub Release")}`],
      {
        encoding: "utf8",
        env: {
          ...process.env,
          PATH: `${root}:${process.env.PATH}`,
          CALL_LOG: callLog,
          RELEASE_STATE: state,
          GITHUB_REF_NAME: "v0.3.0",
        },
      },
    );
    expect(result.status, result.stderr).toBe(status);
    expect((await readFile(callLog, "utf8")).trim().split("\n")).toEqual(calls);
  });

  test("rejects lightweight tags and empty annotated-tag notes before any build", async () => {
    const root = await mkdtemp(join(tmpdir(), "verilex-release-workflow-"));
    temporaryDirectories.push(root);
    const steps = parseSteps();
    const validation = shellStep(steps, "Validate annotated tag and release notes");
    const run = (tag: string) =>
      spawnSync("/bin/bash", ["-c", `set -euo pipefail\n${validation}`], {
        cwd: root,
        encoding: "utf8",
        env: { ...process.env, GITHUB_REF_NAME: tag },
      });
    const git = (...args: string[]) =>
      spawnSync("git", args, { cwd: root, encoding: "utf8" });

    expect(git("init", "-q").status).toBe(0);
    expect(git("config", "user.name", "VeriLex Test").status).toBe(0);
    expect(git("config", "user.email", "test@verilex.invalid").status).toBe(0);
    await writeFile(join(root, "fixture.txt"), "release fixture\n");
    expect(git("add", "fixture.txt").status).toBe(0);
    expect(git("commit", "-q", "-m", "fixture").status).toBe(0);

    expect(git("tag", "v0.3.0-lightweight").status).toBe(0);
    expect(run("v0.3.0-lightweight").status).not.toBe(0);
    expect(git("tag", "-a", "v0.3.0-empty", "-m", "").status).toBe(0);
    expect(run("v0.3.0-empty").status).not.toBe(0);
    expect(git("tag", "-a", "v0.3.0", "-m", "Release notes").status).toBe(0);
    const valid = run("v0.3.0");
    expect(valid.status, valid.stderr).toBe(0);
    expect((await readFile(join(root, "release-notes.md"), "utf8")).trim()).toBe(
      "Release notes",
    );
  });

  test("excludes unsupported release shortcuts, platforms, and insecure transport", () => {
    expect(workflowSource).not.toMatch(
      /workflow_dispatch|pull_request|apple-certificate|notari|x86_64|windows|dangerousInsecureTransportProtocol/i,
    );
  });
});


test("verifies the archive signature against the embedded public key before staging a release", () => {
  const steps = parseSteps("publish");
  const names = steps.map(step => step.name);
  const verification = "Verify updater signature against app public key";
  expect(names.indexOf(verification)).toBeGreaterThan(-1);
  expect(names.indexOf(verification)).toBeLessThan(names.indexOf("Stage draft GitHub Release"));
  expect(shellStep(steps, verification)).toContain("node scripts/verify-updater-signature.mjs");
  expect(shellStep(steps, verification)).toContain("desktop/src-tauri/tauri.conf.json");
});
