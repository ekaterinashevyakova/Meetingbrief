# Backend evaluation

`run_eval.py` runs every case in `dataset.json` through
`generate_briefing()` and saves outputs to `results/<timestamp>/`
(ignored by git).

    python testing/run_eval.py                                   # demo backend
    python testing/run_eval.py --backend briefing_generator --runs 3
    python testing/run_eval.py --from-cache testing/results/<timestamp> --judge

## Checking a saved model output

`--check-file` checks a JSON response without calling the backend. Give the
meeting either as a dataset case or directly:

    python testing/run_eval.py --check-file testing/model_output_example.json --meeting 2026-11-06T14:00:00+01:00 --goal "Discuss a potential partnership"
    python testing/run_eval.py --check-file some_output.json --case test_001 --judge

With `--case`, the case's `expected_facts` and `must_not_contain` are used too.

## Testing a teammate's branch without merging it

Check the branch out into a separate folder with `git worktree`, then point
`--repo` at it. Your own branch stays untouched.

    git fetch origin
    git worktree add ../mb-bifrost origin/marina/bifrost
    python -m pip install -r ../mb-bifrost/requirements.txt
    # put .env with BIFROST_* and TAVILY_API_KEY into ../mb-bifrost
    python testing/run_eval.py --repo ../mb-bifrost --backend briefing_generator

    git worktree remove ../mb-bifrost                            # when done

The response contract is always checked with `validate_result()` from this
checkout's `app.py`, whichever repository the backend comes from.

## Checks

Deterministic: `validate_result()`, `is_demo` is false, event dates inside
the 30-day window, events cite sources, http/https URLs, `researched_at`
format, `must_not_contain` terms (wrong entity), `expect_no_events` cases
(no invented events). Gaps: no blank strings, and a response with no
events must explain why in `gaps`. Warnings: duplicate gaps, undated events, uncited
sources, source `published_date` outside the window.

With `--judge` (needs `JUDGE_BASE_URL`, `JUDGE_MODEL`, and either
`JUDGE_AUTH_HEADER` — the full Authorization value, same as
`BIFROST_BASIC_AUTH` — or `JUDGE_API_KEY` for a Bearer token): importance-weighted fact recall (all / recent /
background), contradicted facts, event groundedness against cited sources
(`article_text` if present, else `excerpt`), unsupported summary claims,
a 1–5 goal-relevance score, `gap_recall` (share of the case's
`expected_gaps` that the briefing mentions), `vague_gaps` (generic items
like "some information may be missing") and `non_limitation_gaps` (items
that state a fact or a guess instead of a limitation).

## Dataset fields

- `expected_facts`: `fact`, `kind` (`recent` = inside the 30-day window,
  `background` = older context), `importance` (0–1), optional `date`, `source`.
- `expected_gaps`: limitations a good briefing should mention for this case.
- `must_not_contain`: terms that signal the wrong entity.
- `expect_no_events`: true when nothing real can be found.

## Unit tests

    python -m pytest testing/app_test.py -v

Offline, no keys. Tests marked xfail document known backend gaps; when one
starts passing, remove its marker.

The demo backend is expected to fail every case.
