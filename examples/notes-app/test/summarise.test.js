import { test } from "node:test";
import assert from "node:assert/strict";
import { handleSummarise } from "../src/api/summarise.js";

test("a note with text gets the model's summary", async () => {
  const model = { complete: async (text) => ({ text: `summary of: ${text}` }) };
  const res = await handleSummarise({ text: "Water the tomatoes." }, { model });
  assert.equal(res.status, 200);
  assert.deepEqual(res.body, { summary: "summary of: Water the tomatoes." });
});
