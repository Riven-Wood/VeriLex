import { mkdtempSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { afterAll, beforeAll, expect, test } from "vitest";
import { verifyUpdaterSignature } from "../../scripts/verify-updater-signature.mjs";

const root = mkdtempSync(join(tmpdir(), "verilex-test-updater-signature-"));
const cli = resolve("../node_modules/.bin/tauri");
let publicKey: string;
let signature: string;
const archive = Buffer.from("VeriLex isolated update test archive\n");

beforeAll(() => {
  const key = join(root, "test.key");
  // Disposable test key; never use the production signing key.
  const generated = spawnSync(cli, ["signer", "generate", "--password", "test-only", "--write-keys", key, "--force"], { encoding: "utf8", env: { ...process.env, CI: "true" } });
  expect(generated.status).toBe(0);
  writeFileSync(join(root, "test.tar.gz"), archive);
  const signed = spawnSync(cli, ["signer", "sign", "--private-key-path", key, "--password", "test-only", join(root, "test.tar.gz")], { encoding: "utf8" });
  expect(signed.status).toBe(0);
  publicKey = readFileSync(`${key}.pub`, "utf8").trim();
  signature = readFileSync(join(root, "test.tar.gz.sig"), "utf8").trim();
}, 30_000);

afterAll(() => rmSync(root, { recursive: true, force: true }));

test("accepts an actual Tauri CLI signature", () => {
  expect(verifyUpdaterSignature(archive, signature, publicKey)).toBe(true);
});

test("rejects modified archive bytes", () => {
  expect(() => verifyUpdaterSignature(Buffer.concat([archive, Buffer.from("modified")]), signature, publicKey)).toThrow("verification failed");
});

test("rejects a different signing key before publication", () => {
  const productionPublicKey = JSON.parse(readFileSync(resolve("src-tauri/tauri.conf.json"), "utf8")).plugins.updater.pubkey;
  expect(() => verifyUpdaterSignature(archive, signature, productionPublicKey)).toThrow("does not match");
});

test("rejects changes to the authenticated comment", () => {
  const lines = Buffer.from(signature, "base64").toString("utf8").trimEnd().split("\n");
  lines[2] += " tampered";
  const changed = Buffer.from(lines.join("\n")).toString("base64");
  expect(() => verifyUpdaterSignature(archive, changed, publicKey)).toThrow("verification failed");
});

test("rejects malformed signatures", () => {
  expect(() => verifyUpdaterSignature(archive, "not-a-signature", publicKey)).toThrow();
});
