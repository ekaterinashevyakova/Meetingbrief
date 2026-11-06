# Backend evaluation

`run_eval.py` runs every case in `dataset.json` through
`generate_briefing()` and saves outputs to `results/<timestamp>/`
(ignored by git).

    python testing/run_eval.py                                   # demo backend
    python testing/run_eval.py --backend briefing_generator --runs 3
    python testing/run_eval.py --from-cache testing/results/<timestamp> --judge

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
(no invented events, gaps explained). Warnings: undated events, uncited
sources, source `published_date` outside the window.

With `--judge` (needs `JUDGE_BASE_URL`, `JUDGE_MODEL`, optional
`JUDGE_API_KEY`): importance-weighted fact recall (all / recent /
background), contradicted facts, event groundedness against cited sources
(`article_text` if present, else `excerpt`), unsupported summary claims, and
a 1–5 goal-relevance score.

The demo backend is expected to fail every case.
