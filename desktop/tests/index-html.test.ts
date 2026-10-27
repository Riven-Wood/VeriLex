import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("desktop document", () => {
  it("declares the VeriLex mark as the favicon", async () => {
    const html = await readFile(resolve(process.cwd(), "index.html"), "utf8");

    expect(html).toContain('rel="icon"');
    expect(html).toContain('href="/verilex-logo.png"');
  });
});
