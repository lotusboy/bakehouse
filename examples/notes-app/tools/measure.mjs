// The app's API outputs as flat key -> value, with the model mocked.
// Usage: node tools/measure.mjs <out.json>
import { writeFileSync } from "node:fs";
import { handleSummarise } from "../src/api/summarise.js";

const cases = {
  ok: { text: "Water the tomatoes on Friday." },
  empty: { text: "" },
  missing: {},
};

const out = {};
for (const [name, body] of Object.entries(cases)) {
  let calls = 0;
  // The mocked model: a fixed reply, so the same request always gives the same output.
  const model = {
    complete: async (prompt) => {
      calls += 1;
      return { text: `summary of ${String(prompt).length} characters` };
    },
  };
  let res;
  try {
    res = await handleSummarise(body, { model });
  } catch (e) {
    res = { status: "threw", body: String(e) };
  }
  out[`summarise.${name}.status`] = res.status;
  out[`summarise.${name}.body`] = JSON.stringify(res.body);
  out[`summarise.${name}.modelCalls`] = calls;
}
writeFileSync(process.argv[2], JSON.stringify(out, null, 2) + "\n");
