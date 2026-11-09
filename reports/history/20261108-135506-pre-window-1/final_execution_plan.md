# PortfolioIQ final execution plan

Target: `gemini-3.6-flash` using the existing project/key. Frozen versions: router `676786d09950`, SQL prompt `d5a914eb71d5`, context `4b74dcd0419a`.

## Pending essential work

| Group | Cases | Request estimate |
|---|---|---:|
| Official agent evaluation | Q1–Q10 | 16 minimum, up to 40 with retries/repairs |
| Agent integration | A2, A3 | 1 minimum, up to 4 |
| Unseen SQL | U9, U10 | 2 minimum, up to 8 |
| **Total** | **19 cases** | **19 minimum, 56 maximum** |

Q9 and Q10 are deterministic exposure calculations in the official evaluator and may consume no Gemini request; Q5/Q7 saved SQL drafts may be reused where their request hash matches. The controller recomputes pending cases from recorded runs and never blindly reruns completed cases.

## Daily allocations

### 8 November 2026 (Window 1)

1. Official Q1–Q10, cap 18 requests.
2. A2/A3, cap 2 requests, only after the official stage and only if the remaining budget is sufficient.

The evaluator processes Q9 before the agent stage, so A2 can reuse valid saved work. The hard window maximum is 20 requests, including failures and retries.

### 9 November 2026 (Window 2)

1. Resume outstanding official cases, cap up to the remaining 20.
2. Resume outstanding A2/A3.
3. Run U9/U10 with whatever capacity remains.

The stage controller stops before the hard cap and skips stages that cannot safely fit the remaining budget.

## Risks and limitations

- Google AI Studio is the authority for quota; the local ledger only counts requests made by this project checkout.
- The quota reset is expected around 13:30 IST in November 2026, but must be verified manually.
- 429, 503, timeout, and retry attempts consume the local cap. Two consecutive failed API calls stop the workflow.
- The workflow intentionally excludes optional stability testing and all Flash-Lite outputs.
- Raw responses and reports are append/timestamp based; do not remove or overwrite prior runs.
