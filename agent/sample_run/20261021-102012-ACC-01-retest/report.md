# DemoBank QA Audit Report

- **Target:** http://localhost:5173  (local copy, database reset between scenarios)
- **Specification:** `../test-website/PRD.md`
- **Run:** `20261021-102012`  ·  started 2026-10-21T10:20:12  ·  duration 1.4 min
- **Models:** gemini-3.5-flash-lite, gemini-3.8-flash  ·  pricing as of 2026-10-21
- **Requirements tested:** 1  ·  **PASS 1**  ·  **FAIL 0**  ·  **UNVERIFIED 0**
- **Total model cost of this run:** **$0.0525** (4 calls, 13,465 input / 11,303 output tokens)

Verdicts follow the evidence model: PASS = observed state matches the PRD; FAIL = evidence shows a meaningful mismatch that reproduced on a fresh re-run and was confirmed by a majority review against the PRD sentence; UNVERIFIED = no reliable conclusion, with the reason stated.

## Executive summary

The test run evaluated the local deployment environment (run ID: 20261021-102012) over 1.4 minutes against defined requirements. Core access functionality met expectations with zero recorded failures in the tested scope.

### Key findings

- Requirement ACC-01 passed deep testing across 2 scenarios: the landing page includes an 'Enter Demo' action that successfully opens the dashboard without requiring passwords or identity verification.

## Results by requirement

| ID | Area | Depth | Verdict | Scenarios | Reason |
|---|---|---|---|---|---|
| [ACC-01](#acc-01) | access | deep | ✅ PASS | 2 pass | 2 scenario(s) passed |

## Passed requirements

### ACC-01

**Requirement:** The landing page provides an Enter Demo action that opens the demo dashboard without requiring a password or real identity information.  
**PRD:** “The landing page provides a clearly labeled **Enter Demo** action. It opens the demo dashboard without requiring a password or real identity information.”  
**Verdict:** ✅ PASS: 2 scenario(s) passed

<details><summary>✅ <i>Landing page displays a clear Enter Demo action that opens the dashboard without requesting credentials</i> (normal, equivalence partitioning)</summary>

#### ✅ Scenario: Landing page displays a clear Enter Demo action that opens the dashboard without requesting credentials

*normal · equivalence partitioning* · all checks passed

**Steps to reproduce**

1. Navigate to /
2. Read text of button 'Enter Demo' → `button_label` = `"Enter Demo"`
3. Read count of 'textbox' → `inputs_count` = `0`
4. Click button 'Enter Demo'
5. Read url of  → `current_url` = `"/dashboard"`
6. Read text of element [data-testid=active-customer] → `active_customer` = `"Signed in as\nAvery Demo"`
7. Read count of element [data-testid=account-card] → `account_cards_count` = `2`
8. Read API GET /api/me → `me_api` = `{"status": 200, "body": {"id": "00000000-0000-0000-0000-000000000001", "display_name": "Avery Demo", "masked_email": "a***@example.test"}}`

| Expected (check) | Kind | Result | Observed |
|---|---|---|---|
| Enter Demo action is present on the landing page without requiring credential input fields (PRD: without requiring a password or real identity information) | functional | ✅ pass | `{"button_label": "Enter Demo", "inputs_count": 0}` |
| Clicking Enter Demo navigates directly to the dashboard view | functional | ✅ pass | `{"current_url": "/dashboard"}` |
| Dashboard views and account cards become visible in the UI | functional | ✅ pass | `{"account_cards_count": 2, "active_customer": "Signed in as\nAvery Demo"}` |
| Session is created server-side and authenticated customer matches UI | cross_source | ✅ pass | `{"active_customer": "Signed in as\nAvery Demo", "me_api": {"status": 200, "body": {"id": "00000000-0000-0000-0000-000000000001", "display_name": "Avery Demo", "masked_email": "a***@example.test"}}}` |
| Enter Demo action is clearly labeled on the landing page (PRD: clearly labeled Enter Demo action) | subjective | ✅ pass judge: Does this button clearly present itself as an action to enter the demo? -> True (The observed text is 'Enter Demo', which directly and clearly states th | `{"button_label": "Enter Demo"}` |

Evidence: [ACC-01-s1-r0-v00-s01.jpg](./screenshots/ACC-01-s1-r0-v00-s01.jpg) · [ACC-01-s1-r0-v00-s04.jpg](./screenshots/ACC-01-s1-r0-v00-s04.jpg) · [ACC-01-s1-r0-v00-final.jpg](./screenshots/ACC-01-s1-r0-v00-final.jpg)

![final state](./screenshots/ACC-01-s1-r0-v00-final.jpg)

Network calls: `POST /api/demo/session 201`, `GET /api/me 200`, `GET /api/accounts 200`

</details>

<details><summary>✅ <i>Re-entering demo via Enter Demo after logging out opens the dashboard without credentials</i> (normal, state-transition testing)</summary>

#### ✅ Scenario: Re-entering demo via Enter Demo after logging out opens the dashboard without credentials

*normal · state-transition testing* · all checks passed

**Steps to reproduce**

1. Open the landing page and enter the demo
2. Click button 'Log out'
3. Navigate to /
4. Read text of button 'Enter Demo' → `logout_landing_btn` = `"Enter Demo"`
5. Click button 'Enter Demo'
6. Read url of  → `reentered_url` = `"/dashboard"`
7. Read count of element [data-testid=account-card] → `reentered_cards` = `2`
8. Read API GET /api/me → `reentered_me_api` = `{"status": 200, "body": {"id": "00000000-0000-0000-0000-000000000001", "display_name": "Avery Demo", "masked_email": "a***@example.test"}}`

| Expected (check) | Kind | Result | Observed |
|---|---|---|---|
| Enter Demo button is accessible again on the landing page after logout | functional | ✅ pass | `{"logout_landing_btn": "Enter Demo"}` |
| Navigates to /dashboard and renders account cards without prompting for credentials | functional | ✅ pass | `{"reentered_cards": 2, "reentered_url": "/dashboard"}` |
| Backend session is successfully established | side_effect | ✅ pass | `{"reentered_me_api": {"status": 200, "body": {"id": "00000000-0000-0000-0000-000000000001", "display_name": "Avery Demo", "masked_email": "a***@example.test"}}}` |

Evidence: [ACC-01-s2-r0-v00-s02.jpg](./screenshots/ACC-01-s2-r0-v00-s02.jpg) · [ACC-01-s2-r0-v00-s03.jpg](./screenshots/ACC-01-s2-r0-v00-s03.jpg) · [ACC-01-s2-r0-v00-s05.jpg](./screenshots/ACC-01-s2-r0-v00-s05.jpg) · [ACC-01-s2-r0-v00-final.jpg](./screenshots/ACC-01-s2-r0-v00-final.jpg)

![final state](./screenshots/ACC-01-s2-r0-v00-final.jpg)

Network calls: `POST /api/demo/session 201`, `GET /api/me 200`, `GET /api/accounts 200`

</details>


## Cost breakdown

Prices: USD per 1M tokens, standard paid tier, as of 2026-10-21. Thinking tokens are billed as output.

| Phase | Calls | Input tokens | Cached | Output tokens | Cost (USD) |
|---|---|---|---|---|---|
| critique | 1 | 7,300 | 0 | 7,081 | $0.0320 |
| plan | 1 | 5,725 | 0 | 3,922 | $0.0190 |
| report | 1 | 378 | 0 | 270 | $0.0013 |
| judge | 1 | 62 | 0 | 30 | $0.0002 |
| **Total** | **4** | **13,465** | **0** | **11,303** | **$0.0525** |

| Model | Calls | Cost (USD) |
|---|---|---|
| gemini-3.8-flash | 4 | $0.0525 |

Average cost per requirement: $0.0512; most expensive: ACC-01 ($0.0512).

## Coverage, gaps and limitations

- **Deep coverage** (normal, negative and boundary scenarios with side-effect checks): ACC-01
- **Light coverage** (main normal path only): 
- **Requirement extraction:** 32 requirements extracted; coverage of a hand-made PRD inventory 36/36
- Testing was conducted on a local development environment (http://localhost:5173) with database resets between scenarios rather than a hosted staging or production target.
- The evaluation run was brief (1.4 minutes), and while coverage recalled 36 items, detailed scenario results were only provided for requirement ACC-01, leaving the remaining areas unverified in this report view.

### Recommendations

- Validate ACC-01 and the remaining requirement suite in a hosted staging environment to verify demo access under realistic network and infrastructure conditions.
- Expand reporting visibility across all 36 covered inventory items to confirm pass/fail statuses beyond the initial access module.

---
Method: [ARCHITECTURE.md](../../docs/ARCHITECTURE.md) · Decisions: [DECISIONS.md](../../docs/DECISIONS.md) · Raw evidence: `results.json`, `llm_calls.jsonl`, `screenshots/` in this run folder.
