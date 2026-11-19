# Tracker

## T1: an empty note gets a 400, and no model call

id: T1
need: POST /api/summarise with no text returns status 400 and the error "text is required", and calls no model.
evidence: node tools/measure.mjs on BASE: summarise.empty and summarise.missing each return status 200 and make 1 model call.
layer: src/api/summarise.js, the request handler: it passes the text to the model without checking it.
gates: unit, api-moves
expected: summarise.empty.status 200 -> 400; summarise.empty.body -> {"error":"text is required"}; summarise.empty.modelCalls 1 -> 0; the same three keys for summarise.missing.
mustNotMove: summarise.ok.*
decided: owner, 2026-11-15
serves: a person who submits an empty note gets a clear message, and no model call is paid for; 2 of the 3 request cases the measure step sends.
