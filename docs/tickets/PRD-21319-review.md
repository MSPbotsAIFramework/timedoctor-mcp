# PRD-21319 · Code review

Status: frozen @ 0f7e0ac

## Round 1 · 2026-11-04 · reviewed dev/PRD-21319 @ cfd855a (base main@e2a17bb)

Verdict: changes requested

### Scope

`git diff main@e2a17bb...cfd855a` on `MSPbotsAIFramework/timedoctor-mcp` — the full new repo (implementation commit `2f2fa30` plus the design/self-test/handoff doc commits). Confirmed `18 passed` on `uv run pytest -q` re-run against `2f2fa30` synced with `main@e2a17bb` (nothing to sync, `main` had not moved); Chinese-character scan clean; README "MSPbots Connector" section byte-identical to `reference/readme-connector-section.md` and placed before the credential header table.

### Findings

1. [must-fix] `src/timedoctor_mcp/client.py:90-99` (`_sleep_before_retry`, called from `get()` at lines 64/68/79) — the retry/backoff loop sleeps for whatever `Retry-After` the vendor returns, with no cap and no tracking of total elapsed time. SOP §14's checklist requires "单次调用总耗时(含重试)必须 < 120s" (`reference/vendor-mcp-development-sop.md:273,454`), and the design's own "Risks & dependencies" section (`docs/tickets/PRD-21319.md:169`) explicitly chose to "treat whatever Time Doctor returns as authoritative (429 + `Retry-After`) rather than hard-coding a number" — i.e. this is a known, unmitigated gap, not an oversight the design missed. Concretely: if Time Doctor returns `Retry-After: 150` on a 429 (a realistic value for a rate-limit response), this one tool call sleeps past the 120s budget the Gateway/caller expects, turning what AC10 wants to be a clean `rate_limited` envelope into an actual upstream timeout — the opposite of AC10's intent. The existing test (`tests/test_tools.py::test_upstream_429_maps_to_rate_limited`) only exercises `Retry-After: 0`, so this path is untested.
   Fix: track elapsed time across the retry loop (or at minimum clamp each `Retry-After`-derived delay, e.g. to ~30s) so the total call time including all retries cannot exceed roughly 100-110s, leaving headroom under the hard 120s limit; stop retrying and raise the typed error immediately once the remaining budget would be exceeded by the next sleep.

2. [must-fix] `src/timedoctor_mcp/tools.py:265-270` (`timedoctor_list_worklogs`) — `has_more` is computed from `all_items`, which is the list **after** the client-side `user_id` filter is applied (line 266-267), not the raw upstream page returned by `_extract_items`. The client-side filter exists, per the design's own implementation note (`docs/tickets/PRD-21319.md:202`'s equivalent in the Self-test section, "belt-and-suspenders... in case the vendor's `user` query parameter is not honored server-side"), specifically for the case where it *does* remove items from a full-size upstream page. When that happens, a page that came back at `limit` size gets filtered down to a smaller `all_items`, `_has_more(all_items, limit, payload)` then compares the smaller post-filter count to `limit`, evaluates `False`, and the tool reports no further pages — even though the next upstream page could hold more of that user's worklogs. A caller paging through one user's worklogs over a date range with more than one page of company-wide data will silently get a truncated result with `has_more: false`, which directly undermines AC4 ("returns the worklog entries in that range, paginated"). The same risk is compounded by the field-name uncertainty the design already flags (`docs/tickets/PRD-21319.md:168`): if `_trim_worklog`'s `_field(w, "user_id", "userId")` doesn't match the real field name, every item in a page would fail the client-side filter and the tool would silently return an empty page instead of surfacing a mismatch. The existing test (`test_list_worklogs_filters_by_user_and_date_range`) uses a 2-item mock page against `limit=50`, so `has_more` is trivially `False` either way and this path is untested.
   Fix: compute `has_more` from the un-filtered upstream page (the `_extract_items(...)` result, before the `user_id` filter), or — if the server-side `user` param is trusted — drop the redundant client-side filter and keep whatever the server returned as authoritative.

### Not re-litigated (acknowledged, non-blocking)

- `docker build --platform linux/amd64` was not run by the dev leg (no `docker` group membership on that host) and not re-attempted here for the same reason — environment gap, not a code defect, per SOP's "no container runtime access" allowance. Dockerfile structure matches the SOP template (multi-stage, non-root, `curl`, `EXPOSE 8080`, `HEALTHCHECK`).
- TC-TD-004B (worklog data against a live account) remains untested — needs a real Time Doctor account, not a blocker per SOP.
- Inferred response-envelope field names (`stats/summary`, `stats/category`, etc.) are an acknowledged risk in the design, mitigated by defensive `_field()`/`_extract_items()` fallbacks for *display* purposes; this round's finding 2 is specifically about the one place (`timedoctor_list_worklogs`'s client-side re-filter) where that same tolerant mapping is also used as a hard filter/pagination criterion, which is a different and riskier use than the mitigation the design describes.

### Fixes (round 1), dev/PRD-21319 @ 46995e5

1. `src/timedoctor_mcp/client.py` — `get()` now records `start = time.monotonic()` and `_sleep_before_retry` takes that `start` plus two new module constants: `MAX_RETRY_DELAY_S = 30.0` (any single delay, including a `Retry-After`-derived one, is clamped to this) and `CALL_BUDGET_S = 100.0` (total elapsed time budget, leaving headroom under the SOP's 120s hard limit). Before sleeping, it checks `elapsed + delay >= CALL_BUDGET_S`; if that holds it returns `False` without sleeping, and all three call sites (`get()`'s timeout/network-error and retryable-status branches) now raise `last_exc` immediately instead of continuing the loop. A `Retry-After: 150` on a 429 is therefore clamped to a 30s sleep, and the loop gives up well before cumulative elapsed time (sleeps + the HTTP round trips themselves, since the check uses wall-clock `time.monotonic()`, not just summed delays) reaches 100s.
   Retest: added `tests/test_client.py` with a fake, explicitly-advanced clock (`monkeypatch`-ed `client_module.time.monotonic` and `client_module.asyncio.sleep`) so the budget math is exact instead of depending on real sleeps:
   - `test_retry_delay_is_clamped_even_for_a_huge_retry_after` — `Retry-After: 150` sleeps for <= `MAX_RETRY_DELAY_S` (30s), not 150s.
   - `test_retry_stops_once_the_call_budget_would_be_exceeded` — each simulated response "costs" 40s of elapsed time before replying 429/`Retry-After: 150`; by the second response elapsed is already 110s, so the client raises `UpstreamError(status_code=429)` instead of sleeping a third clamped 30s delay. Final elapsed stays under 120s.
   - `test_retry_after_zero_is_unaffected_by_the_budget` — regression guard for the existing `Retry-After: 0` fast path (`test_upstream_429_maps_to_rate_limited`).
   Verified these three fail (`ERROR`, `AttributeError` on the not-yet-existing `MAX_RETRY_DELAY_S`/`CALL_BUDGET_S`) against the pre-fix `client.py` (`git stash` of this commit's changes), then pass against the fix — confirmed red before green.

2. `src/timedoctor_mcp/tools.py:265-271` — `timedoctor_list_worklogs` now keeps `raw_items = _extract_items(payload, "data", "worklogs")` (the un-filtered upstream page) separate from `all_items` (the trimmed, then optionally `user_id`-filtered, list returned to the caller), and passes `raw_items` — not `all_items` — into `_has_more(...)`. `has_more` is therefore computed against the actual upstream page size, independent of how many items the client-side `user_id` filter removes.
   Retest: added `tests/test_tools.py::test_list_worklogs_has_more_counts_raw_page_not_filtered_items` — a 2-item upstream page at `limit=2` with only one item matching `user_id`; pre-fix this asserted `has_more is True` and failed with `assert False is True` (confirmed by running it against the pre-fix `tools.py`); post-fix it passes. Wired into `docs/tickets/PRD-21319.ac.json`'s `TC-TD-004.tests` (and the three new client tests into `TC-TD-010.tests`) alongside the existing cases.

Full suite after both fixes: `uv run pytest -q` → `22 passed` (the pre-existing 18 plus these 4 new cases), run against `dev/PRD-21319 @ 46995e5` synced with `main@e2a17bb` (`main` had not moved since round 1, confirmed via `git merge-base --is-ancestor origin/main HEAD`, so no re-merge was needed). Chinese-character scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`) clean except the quoted SOP excerpt inside this review doc's own round-1 finding text, which is pre-existing review content, not code or new docs. `gitleaks` is not installed on this host, so it was not run (per the review's own "no container runtime access"-style allowance for host tooling gaps — not a code defect). Smoke test against `python -m timedoctor_mcp`: `GET /health` → `{"status":"ok"}`; `tools/list` with `X-Timedoctor-Token` → 6 tools listed; `tools/list` with no credential header → `HTTP 401`.

## Round 2 · 2026-11-05 · reviewed dev/PRD-21319 @ 0f7e0ac (base main@e2a17bb)

Verdict: approved

### Scope

`git diff e2bdf0e...0f7e0ac` on `MSPbotsAIFramework/timedoctor-mcp` (the round-1 reviewed sha to the current `[DEV]` sha): `src/timedoctor_mcp/client.py`, `src/timedoctor_mcp/tools.py`, two new/extended test files, the `ac.json` wiring, and this review doc's own round-1 "Fixes" section (written by the dev leg, not re-litigated as a finding). `main` has not moved since round 1 (`git merge-base --is-ancestor origin/main HEAD` holds), so no re-merge was needed before this review.

### Round-1 must-fix verification

1. `src/timedoctor_mcp/client.py` — confirmed fixed. `_sleep_before_retry` now clamps every delay (including `Retry-After`-derived ones) to `MAX_RETRY_DELAY_S=30s` via `min(delay, MAX_RETRY_DELAY_S)` (`client.py:119`), and gates every sleep on `elapsed + delay >= CALL_BUDGET_S` (100s) using a `start = time.monotonic()` recorded once per `get()` call; all three call sites (timeout, network-error, retryable-status) now `raise last_exc` immediately when the gate trips instead of sleeping and retrying. Traced the worst case by hand: with `CONNECT_TIMEOUT_S=5` / `READ_TIMEOUT_S=30`, two full-length requests (≤35s each) plus one clamped 30s sleep lands around ~100s elapsed, at which point the gate stops a third sleep/request — comfortably inside the SOP's 120s hard limit even though the budget check itself only bounds sleeps, not the HTTP calls. `tests/test_client.py`'s three new cases exercise exactly this: a single huge `Retry-After` gets clamped (not slept verbatim), repeated 40s-costing responses trip the budget gate before a third sleep, and the pre-existing `Retry-After: 0` fast path is unaffected. Re-ran `uv run pytest tests/test_client.py -q`: `3 passed`.
2. `src/timedoctor_mcp/tools.py:265-271` — confirmed fixed. `timedoctor_list_worklogs` now keeps `raw_items` (the unfiltered upstream page from `_extract_items`) separate from `all_items` (trimmed + optionally `user_id`-filtered), and passes `raw_items` — not `all_items` — into `_has_more(...)`. The new `test_list_worklogs_has_more_counts_raw_page_not_filtered_items` reproduces the exact scenario from the original finding (a 2-item upstream page at `limit=2`, one item filtered out by `user_id`) and asserts `has_more is True`. Re-ran `uv run pytest tests/test_tools.py -q`: `18 passed` (includes this new case).

Both fixes are the minimal, targeted change the finding asked for — no behavior outside the two call paths changed.

### Findings

None. (0 must-fix, 0 suggestions.)

### Verification run

`uv run pytest -q` on `dev/PRD-21319 @ 0f7e0ac` (fresh `uv sync` in an independent worktree, Python 3.12 via `uv run`): `22 passed`, matching the dev leg's reported count. Chinese-character scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`) clean outside the quoted SOP excerpt in this doc's own round-1 finding text (pre-existing review prose, not code). No secrets/API-key/password literals found in `src/` beyond the `X-Timedoctor-Token` header name and a docstring's own use of the word "non-secret". `gitleaks` not installed on this host (same pre-existing host-tooling gap as round 1).

### Not re-litigated (acknowledged, non-blocking, unchanged from round 1)

- `docker build --platform linux/amd64` not run (no `docker` group membership on review host) — environment gap, not a code defect.
- TC-TD-004B (worklog data against a live account) remains untested — needs a real Time Doctor account, not a blocker per SOP.
- Inferred response-envelope field names remain an acknowledged design risk, mitigated by defensive `_field()`/`_extract_items()` fallbacks.
