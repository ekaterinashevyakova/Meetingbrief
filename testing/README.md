# Backend evaluation

`run_eval.py` runs every case in `dataset.json` through
`generate_briefing()` and saves outputs to `results/<timestamp>/`
(ignored by git).

    python -m pip install -r testing/requirements.txt
    python testing/run_eval.py                                   # demo backend
    python testing/run_eval.py --backend briefing_generator --runs 3
    python testing/run_eval.py --repo ../mb-bifrost --backend briefing_generator --judge
    python testing/run_eval.py --from-cache testing/results/<timestamp> --judge

`--from-cache` re-scores saved outputs without calling the backend again,
so a judge run costs only the judge's tokens.

## Reports, time and tokens

Every run writes to `results/<timestamp>/`:

- `<case>_run<n>.json`: request, result, error, backend seconds and the
  backend's per-step usage.
- `report.json`: checks, judge verdicts, scores and usage per run.
- `report.xlsx` (another path with `--xlsx PATH`):
  - `Summary`: one row per case and run (status, scores, time, tokens,
    estimated cost) and a total / mean row. `verdict` is `OK` (green),
    `ISSUES` (yellow: checks passed, but the judge or the source check
    found problems) or `FAIL` (red: a check failed or something raised).
    `comments` says in plain words what is broken (with the event, claim or
    fact and the judge's reason) and what is fine. The total row's
    `comments` is an overview of the run with the most common problems,
    which also goes to `Run` and `history.xlsx`;
  - `Criteria`: what each score measures;
  - `Facts`, `Events`, `Gaps`, `Review`: judge verdicts with reasons;
  - `Sources`: `--fetch-sources` result per source (status, excerpt match);
  - `Checks`: deterministic failures and warnings;
  - `Usage`: one row per Tavily search, generation call and judge call;
  - `Unit tests`: every test in `app_test.py` with its outcome (passed,
    failed, xfailed = known backend gap, skipped) and message. The unit
    tests run at the start of every evaluation (skip with
    `--skip-unit-tests`); the backend tests (Part 2) run against `--repo`.
    A failing unit test makes the run exit non-zero;
  - `Stability`: per case and score, the number of runs, mean, min, max,
    spread and standard deviation. Use `--runs 3` or more. Scores that
    move by 0.3 or more (0–1 scale) or 1.5 or more (1–5 scale) are listed as
    unstable in the run overview;
  - `Run`: models, settings and totals.

Each run also appends one row (settings, mean scores, unit test counts,
totals) to `results/history.xlsx`, to compare runs over time. If the file is
open in Excel, the row waits in `results/history_pending.json` and is added
on the next run.

Time is split into Tavily search, generation and judge. Backend steps are
timed by wrapping the backend module's `search_sources()` and
`generate_text()`. Generation tokens come from the LLM client's optional
`pop_usage()` (`anthropic_client` or `bifrost_client`); without it the token
columns stay empty. Costs are estimates from `PRICES` in `run_eval.py`.

## Models

The agent generates on Sonnet and the judge is Opus: `AGENT_MODEL =
claude-sonnet-5-5` and `JUDGE_MODEL = claude-opus-5-5` in `run_eval.py`.
The runner sets `ANTHROPIC_MODEL` (read by the backend's `anthropic_client`)
to `AGENT_MODEL` unless it is already set in the environment or `.env`; the
judge uses `JUDGE_MODEL` unless the environment variable `JUDGE_MODEL`
overrides it. If a call reports another model (for example after a
server-side fallback), the run gets a `model mismatch` warning and the
verdict `ISSUES`. The models actually used are in the `Run` sheet
(`generation_models`, `judge_model`) and printed at the end of the run.

## Checking a saved model output

`--check-file` checks a JSON response without calling the backend. Give the
meeting either as a dataset case or directly:

    python testing/run_eval.py --check-file testing/model_output_example.json --meeting 2026-11-06T14:00:00+01:00 --goal "Discuss a potential partnership"
    python testing/run_eval.py --check-file some_output.json --case test_001 --judge

With `--case`, the case's `expected_facts` and `must_not_contain` are used too.


## Checks

Deterministic: `validate_result()`, `is_demo` is false, event dates inside
the 30-day window, events cite sources, http/https URLs, `researched_at`
format, `must_not_contain` terms (wrong entity), `expect_no_events` cases
(no invented events). Gaps: no blank strings, and a response with no
events must explain why in `gaps`. Warnings: duplicate gaps, undated events, uncited
sources, source `published_date` outside the window.

## LLM judge (`--judge`)

| Score | Measures | Scale |
|---|---|---|
| `recall_all` / `recall_recent` / `recall_background` | Expected facts stated in the briefing, weighted by `importance` (all / inside the window / older) | 0–1 ↑ |
| `contradicted` | Expected facts the briefing contradicts | count ↓ |
| `groundedness` | Events whose every statement is supported by the cited sources | 0–1 ↑ |
| `date_accuracy` | Events dated with the date of the event itself, not a publication date; `null` counts when the sources give none | 0–1 ↑ |
| `entity_precision` | Events about the requested company or person, not a namesake (subsidiaries and brands count) | 0–1 ↑ |
| `in_window_rate` / `in_window_unknown` | Events the sources place inside the window, among those with known timing / events with unknown timing | 0–1 ↑ / count ↓ |
| `event_relevance` / `relevant_event_rate` | Relevance of each event to the meeting goal (5 directly matters, 3 background, 1 unrelated): mean / share rated 3 or higher | 1–5 ↑ / 0–1 ↑ |
| `summary_supported` / `summary_unsupported` | Summary claims the sources do not support | bool / count ↓ |
| `gap_recall` | Case's `expected_gaps` that the briefing mentions | 0–1 ↑ |
| `vague_gaps` / `non_limitation_gaps` | Generic gap items / items that state a fact or a guess | count ↓ |
| `goal_score` | Usefulness of questions and discussion points for the goal | 1–5 ↑ |
| `duplicate_events` | Events that repeat another event in different words | count ↓ |
| `overall_score` | How well the briefing prepares someone for the meeting | 1–5 ↑ |

Calls per case: one for all facts, one per event (groundedness, date,
entity, window and goal relevance together), and one each for the summary, goal, every
expected gap, gap quality and the overall review. Evidence is each cited
source's page fetched by `--fetch-sources` when available, else the
backend's `article_text`, else its `excerpt`, cut to
`JUDGE_MAX_SOURCE_CHARS` (default 20000). The `evidence` column of the
Events sheet says which one the judge saw. A judge error is reported for its case and does not stop
the run.

Configuration, from the environment or the repository's `.env`:

- `JUDGE_PROVIDER`: `anthropic` or `openai`. Default: `openai` when
  `JUDGE_BASE_URL` is set, otherwise `anthropic`.
- Anthropic: `ANTHROPIC_API_KEY`; `JUDGE_MODEL` (default `claude-opus-5-5`);
  `JUDGE_EFFORT` (default `high`).
- OpenAI-compatible (e.g. Bifrost): `JUDGE_BASE_URL`, `JUDGE_MODEL`, and
  either `JUDGE_AUTH_HEADER` (the full Authorization value, same as
  `BIFROST_BASIC_AUTH`) or `JUDGE_API_KEY` (Bearer token).

## Independent source check (`--fetch-sources`)

`excerpt` and `article_text` come from the backend, so a judge that only
reads them cannot catch an invented or distorted quote. With
`--fetch-sources` the runner downloads every source URL itself (standard
library, no keys) and:

- checks that the backend's `excerpt` is on the page. Matching is by
  5-word sequences, ignoring case and punctuation; at least 60% of them
  must be found (`EXCERPT_MATCH_MIN`). A miss is a warning;
- gives the judge the page text, starting just before the excerpt, instead
  of the backend's text.

| Score | Measures | Scale |
|---|---|---|
| `excerpt_verified` | Fetched sources whose excerpt is on the page | 0–1 ↑ |
| `sources_unreachable` | Pages that failed to load (HTTP error, timeout) or had under 300 characters of text (paywall, JavaScript-only page); the judge falls back to the backend's text for them | count ↓ |

Pages are cached in `results/<timestamp>/pages/`, so `--from-cache
--fetch-sources --judge` re-uses them. Results per source are in the
`Sources` sheet, and download time is the `fetch_s` column. It works
without `--judge` as well.

    python testing/run_eval.py --repo ../mb-bifrost --backend briefing_generator --fetch-sources --judge

## Dataset fields

Each entry in `dataset.json` is one entity and goal:

```json
{
  "id": "jensen_huang",
  "description": "What the case checks",
  "request": {"entity_type": "person", "name": "Jensen Huang",
              "details": "Co-founder and CEO of Nvidia", "timezone": "UTC",
              "goal": "Discuss the legal problems of his company"},
  "meetings": [
    {"datetime": "2026-10-20T17:00:00+00:00",
     "must_not_contain": ["affair", "spy"]},
    "2026-11-12T16:00:00+00:00"
  ],
  "expected_facts": [
    {"fact": "Nvidia's board opened an investigation ...",
     "date": "2026-11-08", "importance": "high", "source": "https://..."}
  ],
  "expected_gaps": ["..."],
  "must_not_contain": ["..."],
  "expect_no_events": false
}
```

- `request.meeting_datetime` for one meeting, or `meetings` for several. A
  meeting is an ISO 8601 string with a UTC offset, or an object with
  `datetime` and any of `must_not_contain`, `expected_gaps`,
  `expect_no_events`, `description` that replace the case's values for that
  meeting. With several meetings, each runs as its own case
  `<id>@<YYYY-MM-DD>`; `--only <id>` runs all of them.
- `expected_facts`: `fact`, optional `date` (`YYYY-MM-DD`, when it happened
  or was first reported), `importance` (`high`, `medium` (default) or `low`,
  weighted 1.0 / 0.6 / 0.3 in recall), optional `source` (a URL, or a list
  of `{"title", "url"}` objects that may also carry `"paywall": true`,
  `"paid": true` for sponsored content or press releases, and a `"note"`).
  A dated fact must be at most 30 days before the meeting: `load_dataset()`
  stops with an error otherwise. Mark older context that a good briefing
  should still mention with `"background": true`, or set
  `"allow_old_facts": true` on a meeting where old facts are the point
  (for example a meeting long after the news). There is no
  `kind` field: it is computed for each meeting from `date`:
  - `recent`: inside that meeting's 30-day window;
  - `background`: before the window, or no date;
  - `future`: after the meeting. The briefing must not mention it, since the
    attendee could not have known it; if the judge finds it, the run fails
    (`future_leaks`). Use the same fact with several meetings to check this.
- `expected_gaps`: limitations a good briefing should mention for this case.
- `must_not_contain`: terms that signal the wrong entity, or a leak.
- `expect_no_events`: true when nothing real can be found.

The judge matches facts and gaps by meaning, not wording: paraphrases,
rounded numbers, less detail or a mention only in a question or limitation
count as `covered`; touching the development but missing its key point is
`partial` and counts half.

## Unit tests

    python -m pytest testing/app_test.py -v

Offline, no keys. Tests marked xfail document known backend gaps; when one
starts passing, remove its marker.

