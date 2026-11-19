// POST /api/summarise: sends a note's text to an AI model and returns the model's summary.
// The model client is passed in, so a test or the measure step can mock it.
// Work order T1 (docs/TRACKER.md) is the defect to fix: an empty or missing text still calls the model.
export async function handleSummarise(body, { model }) {
  const reply = await model.complete(body.text);
  return { status: 200, body: { summary: reply.text } };
}
