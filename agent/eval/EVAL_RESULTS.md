# Model validation eval (DECISIONS.md D13)

Three requirements of increasing difficulty, run end to end on the local target with
`gemini-3.8-flash` (thinking roles) and `gemini-3.5-flash-lite` (step recovery). Ground truth
was established independently of the agent, with direct API calls against a freshly reset database.

| Tier | Requirement | Plans valid | Conclusive | Agent verdict | Ground truth (hand-checked) | Match | Model cost |
|---|---|---|---|---|---|---|---|
| Easy | ACC-01: Enter Demo opens the dashboard without credentials | 3/3 | 3/3 | PASS | Landing has no password field; `/api/me` 401 before, 200 after; lands on `/dashboard` | ✅ | $0.0495 |
| Medium | TXN-03: every displayed row satisfies all selected filters | 2/2 (one after 1 revision) | 2/2 | PASS | `GET /api/transactions?type=deposit&status=completed` returns only deposit/completed rows | ✅ | $0.1007 |
| Hard | TRF-04: a transfer exceeding the balance is rejected, balances and history unchanged | 3/3 | 3/3 | FAIL | `POST /api/transfers` of 4369.76 from a 4369.75 balance → HTTP 201, balance becomes −0.01 | ✅ | $0.146 |

**Result: 3/3 verdicts match the ground truth.**

## What the eval caught in the agent (and how it was fixed)

1. **Runaway generation.** The first plan call produced 65,522 output tokens of repeated JSON and was
   cut off. Fix: per-phase output caps, one retry on unparsable output, and failed calls are
   now billed in the ledger (they had been recorded at $0).
2. **Vacuous checks.** In TRF-04, two checks passed although a transfer record *was* created: one
   measured `len()` of the API body object (always 1) instead of its nested list, and one looked up
   a table column by a name that CSS had uppercased. Fix: explore now records each endpoint's
   response shape from OpenAPI, the planner is told the exact shapes, table headers are read
   without CSS transforms, and `get()` matches keys case-insensitively. After the fix, all four
   checks in each TRF-04 scenario agree.
3. **The FAIL review working as intended.** In TXN-03, a date-range scenario failed because the
   test itself assumed a matching transaction existed. All three reviewers rejected the
   expectation ("the PRD does not guarantee any arbitrary filter combination returns rows"), the
   plan was revised, and the revised scenario passed. Without the review this would have been a
   false positive.

Initial INTERPRET recall against the hand-made PRD inventory (`prd_inventory.json`): 31/36 → 36/36
after quote validation learned to accept multi-sentence quotes and repair near-verbatim ones.
