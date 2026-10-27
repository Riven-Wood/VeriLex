import { createHash, createPublicKey, verify } from "node:crypto";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

// Tauri wraps the Minisign public key/signature text in base64. Use Node's
// Ed25519 implementation; verify both the archive and authenticated comment.
function decode(value) {
  const text = value.trim();
  const bytes = Buffer.from(text, "base64");
  if (!text || bytes.toString("base64") !== text) throw new Error("invalid base64");
  return bytes;
}

export function verifyUpdaterSignature(archive, encodedSignature, encodedPublicKey) {
  const publicLines = decode(encodedPublicKey).toString("utf8").trimEnd().split(/\r?\n/);
  const lines = decode(encodedSignature).toString("utf8").trimEnd().split(/\r?\n/);
  if (publicLines.length !== 2 || lines.length !== 4 || !lines[2].startsWith("trusted comment: ")) {
    throw new Error("invalid Minisign envelope");
  }
  const publicPacket = decode(publicLines[1]);
  const packet = decode(lines[1]);
  const globalSignature = decode(lines[3]);
  if (publicPacket.length !== 42 || packet.length !== 74 || globalSignature.length !== 64) {
    throw new Error("invalid Minisign packet");
  }
  const algorithm = packet.subarray(0, 2).toString("ascii");
  if (!["Ed", "ED"].includes(algorithm) || !["Ed", "ED"].includes(publicPacket.subarray(0, 2).toString("ascii"))) {
    throw new Error("unsupported Minisign algorithm");
  }
  if (!publicPacket.subarray(2, 10).equals(packet.subarray(2, 10))) {
    throw new Error("updater signing key does not match the app public key");
  }
  const key = createPublicKey({
    key: Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), publicPacket.subarray(10)]),
    format: "der", type: "spki",
  });
  const signature = packet.subarray(10);
  const message = algorithm === "ED" ? createHash("blake2b512").update(archive).digest() : archive;
  const comment = Buffer.concat([signature, Buffer.from(lines[2].slice(17), "utf8")]);
  if (!verify(null, message, key, signature) || !verify(null, comment, key, globalSignature)) {
    throw new Error("updater signature verification failed");
  }
  return true;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const [archive, signature, config, ...extra] = process.argv.slice(2);
    if (!archive || !signature || !config || extra.length) throw new Error("expected archive, signature and Tauri config paths");
    verifyUpdaterSignature(readFileSync(archive), readFileSync(signature, "utf8"),
      JSON.parse(readFileSync(config, "utf8")).plugins.updater.pubkey);
    process.stdout.write("Updater signature matches the app public key.\n");
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : "signature verification failed"}\n`);
    process.exitCode = 1;
  }
}
