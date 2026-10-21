# Fault injection: does the agent catch bugs we planted on purpose?

`scripts/fault_injection.py` copies the DemoBank backend to a temporary folder, plants exactly one
bug, and serves it on separate ports (backend 8001, frontend 5174). The agent then tests the
requirement that bug should break. The kit source and the normal servers are never modified, and
every fault targets a requirement that **passed** in the official run (`sample_run/20261021-092147`),
so a FAIL can only come from the planted bug.

## Round 1: agent as used for the official run

| Planted fault | Requirement | Result | What happened |
|---|---|---|---|
| Same-account transfers no longer rejected | TRF-02 | ✅ **caught** (FAIL) | All 3 scenarios failed: the transfer went through and no explanation was shown |
| Destination credited 1¢ less than source debited | TRF-05 | ✅ **caught** (FAIL) | The money-conservation invariant ("combined balances unchanged by an internal transfer") flagged the missing cent |
| Status filter silently ignored | TXN-03 | ❌ missed (PASS) | The plan filtered `type=transfer` **and** `status=completed`. Every transfer is completed, so this combination can't reveal an ignored status filter |
| Profile email shown unmasked | PRO-01 | ❌ missed (PASS) | The agent *observed* `avery.demo@example.test` but only checked that the text contained "@" and "."; it never checked the "masked" property |
| Balances formatted with 1 decimal | DSH-02 | ⚠️ not observable here (PASS) | The frontend pads to two decimals ($4,369.80), so the *format* requirement still holds; the bug changes the *value* (.75 → .80), which belongs to DSH-04 (balances agree with transactions) |

**Round 1 detection: 2 of 4 observable faults (2 of 5 planted).**

### Root cause and fix (DECISIONS.md D28)

Both real misses had the same cause: **checks that would still pass if the property were broken**.
Two general test-design rules were added to the planner and to its self-critique (no
DemoBank-specific hints):

1. **Property coverage:** every property word in the PRD quote ("masked", "two decimal places",
   "inclusive", "only", "every") needs a check that would *fail* if the property were absent.
2. **Discriminating data:** a filter test only counts if the unfiltered data contains rows that
   should be excluded. Each filter dimension is tested on its own, asserting the excluded rows
   existed before filtering and are gone afterwards.

## Round 2: after the fix

| Planted fault | Requirement | Round 1 | Round 2 | Notes |
|---|---|---|---|---|
| Same-account transfers allowed | TRF-02 | ✅ caught | ✅ caught | |
| Destination credited 1¢ short | TRF-05 | ✅ caught | ✅ caught | Invariant + exact-amount checks both failed |
| Profile email unmasked | PRO-01 | ❌ missed | ✅ **caught** | New check: "Email address is masked (PRD: masked email address)" |
| Status filter ignored | TXN-03 | ❌ missed | ❌ missed | Now uses discriminating data for type, account and date, but status is still only tested *combined* with account and type, where every matching row is already "completed". A third rule (enumeration coverage) moved status into a plan but not on its own. **Known miss** |
| Balances formatted with 1 decimal | DSH-02 | ⚠️ n/a | ⚠️ n/a | Not observable through this requirement (the UI pads to two decimals) |

**Round 2 detection: 3 of 4 observable faults.**

**No new false positives:** the same three requirements re-run on the *healthy* app with the stricter
rules all still PASS (`PRO-01`, `TXN-03`, `DSH-02`; two separate healthy runs).

**Cost of the whole fault-injection study:** $0.74 in model calls for 11 targeted runs (round 1:
$0.32, round 2: $0.36, extra status-filter re-test: $0.07; each run explores the faulty stack from
scratch). The healthy-app false-positive checks cost a further $0.31.

## Honest reading

- The agent reliably catches faults that change **money or records** (balances, transfers, invariants).
- It was weakest on **property words** ("masked") until the planner was told to mutation-test its
  own checks, and it is still weak on **choosing data that isolates one filter dimension** when the
  PRD lists several together. A code-level coverage check (each enumerated item must appear alone
  in some plan) is the natural next step. It was not built for lack of time.
- We stopped iterating after round 2 on purpose: tuning the prompts until this five-fault suite
  scores 5/5 would be over-fitting to our own test, not making the agent better.
