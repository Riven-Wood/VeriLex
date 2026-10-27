import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const STABLE_SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/;
const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/;
const REPOSITORY = "Riven-Wood/VeriLex";
const ARTIFACT_NAME = "VeriLex.app.tar.gz";

function isRfc3339(value) {
  const match = RFC3339.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, offsetHour, offsetMinute] =
    match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const calendarDate = new Date(Date.UTC(year, month - 1, day));
  return (
    calendarDate.getUTCFullYear() === year &&
    calendarDate.getUTCMonth() === month - 1 &&
    calendarDate.getUTCDate() === day &&
    Number(hourText) <= 23 &&
    Number(minuteText) <= 59 &&
    Number(secondText) <= 59 &&
    (offsetHour === undefined || Number(offsetHour) <= 23) &&
    (offsetMinute === undefined || Number(offsetMinute) <= 59)
  );
}

export function createUpdaterManifest(input) {
  if (!input || typeof input !== "object") throw new Error("manifest input is required");
  if (typeof input.version !== "string" || !STABLE_SEMVER.test(input.version)) {
    throw new Error("version must be stable SemVer");
  }
  if (typeof input.notes !== "string" || input.notes.trim() === "") {
    throw new Error("notes must not be empty");
  }
  if (
    typeof input.pubDate !== "string" ||
    !isRfc3339(input.pubDate)
  ) {
    throw new Error("pub-date must be RFC3339");
  }
  if (typeof input.signature !== "string" || input.signature.trim() === "") {
    throw new Error("signature must not be empty");
  }
  if (input.repository !== REPOSITORY) {
    throw new Error(`repository must be ${REPOSITORY}`);
  }
  const expectedTag = `v${input.version}`;
  if (input.tag !== expectedTag) throw new Error(`tag must be ${expectedTag}`);
  if (input.artifactName !== ARTIFACT_NAME) {
    throw new Error(`artifact must be ${ARTIFACT_NAME}`);
  }

  return {
    version: input.version,
    notes: input.notes,
    pub_date: input.pubDate,
    platforms: {
      "darwin-aarch64": {
        signature: input.signature,
        url: `https://github.com/${REPOSITORY}/releases/download/${expectedTag}/${ARTIFACT_NAME}`,
      },
    },
  };
}

function parseArguments(arguments_) {
  const allowed = new Set([
    "--version",
    "--notes-file",
    "--pub-date",
    "--signature-file",
    "--output",
  ]);
  const values = new Map();

  for (let index = 0; index < arguments_.length; index += 2) {
    const flag = arguments_[index];
    const value = arguments_[index + 1];
    if (!allowed.has(flag) || value === undefined || values.has(flag)) {
      throw new Error("expected each required manifest option exactly once");
    }
    values.set(flag, value);
  }
  if (values.size !== allowed.size) {
    throw new Error("missing required manifest option");
  }
  return values;
}

function runCli() {
  const arguments_ = parseArguments(process.argv.slice(2));
  const version = arguments_.get("--version");
  const manifest = createUpdaterManifest({
    version,
    notes: readFileSync(arguments_.get("--notes-file"), "utf8").trimEnd(),
    pubDate: arguments_.get("--pub-date"),
    repository: REPOSITORY,
    tag: `v${version}`,
    artifactName: ARTIFACT_NAME,
    signature: readFileSync(arguments_.get("--signature-file"), "utf8").trim(),
  });
  writeFileSync(arguments_.get("--output"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    runCli();
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
