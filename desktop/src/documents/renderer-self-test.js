"use strict";

let cspViolations = 0;
let cspDirective = "none";

window.addEventListener("securitypolicyviolation", (event) => {
  cspViolations += 1;
  if (
    ["script-src", "connect-src", "worker-src", "style-src", "default-src"].includes(
      event.effectiveDirective,
    )
  ) {
    cspDirective = event.effectiveDirective;
  } else {
    cspDirective = "other";
  }
});

async function finish(ready, errorCode) {
  await new Promise((resolve) => setTimeout(resolve, 100));
  await window.__TAURI_INTERNALS__.invoke("document_renderer_self_test_complete", {
    result: {
      fixture_id: "safe-basic",
      ready,
      csp_violations: cspViolations,
      csp_directive: cspDirective,
      error_code: errorCode,
    },
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  try {
    const response = await fetch("./safe-basic.docx", {
      cache: "no-store",
      credentials: "omit",
    });
    if (!response.ok) {
      await finish(false, "fixture_unavailable");
      return;
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    let renderer;
    let html;
    try {
      const { createWorkerDocxodus } = await import("./worker-proxy.bundle.js");
      renderer = await createWorkerDocxodus({
        wasmBasePath: new URL("./wasm/", location.href).href,
      });
      html = await renderer.convertDocxToHtml(bytes);
    } catch (_) {
      await finish(false, "renderer_conversion_failed");
      return;
    } finally {
      renderer?.terminate();
    }
    if (typeof html !== "string" || html.length === 0 || !html.includes("<")) {
      await finish(false, "renderer_output_invalid");
      return;
    }
    const renderedDocument = document.getElementById("rendered-document");
    const template = document.createElement("template");
    template.innerHTML = html;
    const stylesheetLoads = [];
    for (const style of template.content.querySelectorAll("style")) {
      const stylesheet = document.createElement("link");
      stylesheet.rel = "stylesheet";
      stylesheet.href = URL.createObjectURL(
        new Blob([style.textContent ?? ""], { type: "text/css" }),
      );
      stylesheetLoads.push(
        new Promise((resolve, reject) => {
          stylesheet.addEventListener("load", resolve, { once: true });
          stylesheet.addEventListener("error", reject, { once: true });
        }),
      );
      style.replaceWith(stylesheet);
    }
    renderedDocument.replaceChildren(template.content);
    try {
      await Promise.race([
        Promise.all(stylesheetLoads),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("stylesheet timeout")), 5000),
        ),
      ]);
    } catch (_) {
      await finish(false, "renderer_stylesheet_failed");
      return;
    }
    const layoutHeight = renderedDocument.getBoundingClientRect().height;
    if (
      renderedDocument.childElementCount === 0 ||
      layoutHeight <= 0
    ) {
      await finish(false, "renderer_layout_invalid");
      return;
    }
    document.getElementById("status").textContent = "ready";
    await finish(true, "none");
  } catch (_) {
    await finish(false, "self_test_failed");
  }
});
