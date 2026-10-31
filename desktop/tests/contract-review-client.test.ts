import {afterEach, expect, test, vi} from "vitest";
import {contractReviewApi} from "../src/runtime/contractReviewClient";

afterEach(() => vi.unstubAllGlobals());

test("contract upload keeps requirements out of URL and preserves file in multipart body", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({review_id: "r", thread_id: "t", upload_id: "u", filename: "样本.docx"})));
  vi.stubGlobal("fetch", fetch);
  const file = new File([new Uint8Array([80, 75, 3, 4, 0, 255])], "样本.docx");
  await contractReviewApi.create(file, {skill: "通用合同审查", party: "party_b", strictness: "normal", signature: "V", retrieval: false, requirements: "重点审查付款\n保留原条款", customParty: "丙方", providerId: "p", model: "m"});
  const [url, init] = fetch.mock.calls[0];
  expect(new URL(url).searchParams.get("filename")).toBe("样本.docx");
  expect(init.body.get("file")).toBe(file);
  expect(init.body.get("requirements")).toBe("重点审查付款\n保留原条款");
  expect(init.body.get("custom_party")).toBe("丙方");
  expect(new URL(url).searchParams.has("custom_party")).toBe(false);
  expect(new URL(url).searchParams.has("requirements")).toBe(false);
  expect(init.headers.has("Content-Type")).toBe(false);
});
