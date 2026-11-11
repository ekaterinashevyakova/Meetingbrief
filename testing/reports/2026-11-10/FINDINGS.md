# Evaluation results, 10 November 2026

Full results: [report.xlsx](report.xlsx) (Summary sheet: verdict and plain-words comments per case; other sheets: facts, events, sources, gaps, unit tests, usage) and [report.json](report.json).

## Setup

- **Backend:** `briefing_generator` from Katya's `katya/frontend-integration` merged with `marina/bifrost`, with Bifrost replaced by direct Anthropic API calls (`anthropic_client.py`). This combined branch (`integration`, commit `4c2d071`) is local and has not been pushed yet.
- **Models:** the agent runs on `claude-sonnet-5-5` and the judge is `claude-opus-5-5`.
- **Options:** `--fetch-sources` (the eval downloads every source page itself and judges against it) and `--judge`, one run per case.
- **Dataset:** 19 cases from `testing/dataset.json`, including 5 meeting dates for Jensen Huang.
- **Cost:** $7.16 in total, of which the judge was about 85%.
- **Network failures:** in the first pass, 10 cases failed because of network problems (Anthropic connection drops, empty Tavily responses). Those 10 were rerun once the network was stable, and this report merges both passes.

Unit tests (`app_test.py`, backend tests against the integration branch): **96 passed, 0 failed, 3 xfail** (known backend gaps).

## Overall

**2 OK, 13 with issues, 4 failed.**

| Mean score                                 | Value |
| ------------------------------------------ | ----- |
| Fact recall (weighted)                     | 0.49  |
| Groundedness (events supported by sources) | 0.60  |
| Summary supported by sources               | 0.63  |
| Event date accuracy                        | 0.79  |
| Events inside the 30-day window            | 0.67  |
| Entity precision (right company or person) | 0.95  |
| Event relevance to the goal (1–5)         | 3.47  |
| Excerpts found on the source page          | 0.90  |
| Facts from after the meeting (leaks)       | 0     |
| Overall score (1–5)                       | 3.47  |

## Per case

| Case                                           | recall | grounded | dates | entity | in window | relevance | summary ok | overall | Verdict         |
| ---------------------------------------------- | ------ | -------- | ----- | ------ | --------- | --------- | ---------- | ------- | --------------- |
| test_001 OpenAI                                | 0      | 0.5      | 0.62  | 0.88   | 0.6       | 3.12      | no         | 4       | FAIL            |
| test_002 Sam Altman (Disney), hire             | 0.62   | 1        | 1     | 0.5    | –        | 3.5       | yes        | 3       | FAIL            |
| test_003 Sam Altman (Disney), project          | 0      | –       | –    | –     | –        | –        | yes        | 3       | ISSUES          |
| test_004 made-up company                       | –     | –       | –    | –     | –        | –        | yes        | 3       | OK              |
| test_005 made-up person                        | –     | –       | –    | –     | –        | –        | yes        | 4       | OK              |
| test_006 IKEA                                  | 0.33   | 1        | 1     | 1      | 0.5       | 2.86      | no         | 4       | FAIL            |
| gordeliy                                       | 0      | –       | –    | –     | –        | –        | yes        | 2       | ISSUES          |
| fei_fei_li                                     | 0.53   | 0.5      | 1     | 1      | 1         | 3.5       | no         | 4       | ISSUES          |
| jensen_huang@2026-10-20 (before the news)      | 1      | 0.67     | 1     | 1      | 0.67      | 2         | yes        | 3       | ISSUES, no leak |
| jensen_huang@2026-11-09                        | 1      | 0.62     | 0.75  | 1      | 0.67      | 4.12      | no         | 4       | ISSUES          |
| jensen_huang@2026-11-12                        | 1      | 0.62     | 0.75  | 1      | 0.83      | 4.12      | no         | 4       | ISSUES          |
| jensen_huang@2026-12-04                        | 1      | 0.5      | 0.75  | 1      | 0.86      | 4.25      | yes        | 4       | ISSUES          |
| jensen_huang@2027-01-20 (window in the future) | 0      | –       | –    | –     | –        | –        | yes        | 2       | ISSUES          |
| gelfand                                        | 0      | –       | –    | –     | –        | –        | yes        | 3       | ISSUES          |
| plata                                          | 0.69   | 0.4      | 0.8   | 1      | 0.4       | 4.6       | yes        | 4       | ISSUES          |
| michelin                                       | 0      | 0        | 0     | 1      | –        | 2         | no         | 3       | ISSUES          |
| pop_mart                                       | 0.19   | 1        | 0.8   | 1      | 1         | 3.8       | no         | 4       | ISSUES          |
| kelloggs                                       | 1      | 0        | 0.75  | 1      | 0.5       | 3.25      | yes        | 4       | ISSUES          |
| bending_spoons                                 | 1      | 1        | 1     | 1      | 0.33      | 4         | yes        | 4       | FAIL            |

A dash means the score does not apply. For example, there is no groundedness when the briefing has no events.

## What works

- **No invented content for made-up entities.** The briefing has no events and explains why in `gaps`, for both test_004 and test_005.
- **No information from after the meeting.** For a meeting on 20 October, the briefing does not mention Jensen Huang's affair or the share drop (8–9 November), even though the research ran on 10 November.
- **Good coverage when news exists.** Recall is 1.0 for Jensen Huang after the news, Bending Spoons and Kellogg's. The Kellogg's briefing correctly explains the split between WK Kellogg Co (Ferrero) and Kellanova (Mars).
- **Quotes match the pages.** 90% of the excerpts are really on the source pages.

## Problems, by owner

### Briefing generation (Marina)

1. **Events outside the window.**
   - *Older events:* the agent takes events from older articles or timelines (OpenAI: 25 September; Bending Spoons: IPO on 30 June, deals in September).
   - *Planned future events:* the agent treats them as events that already happened (IKEA: a store opening on 18 November and an appointment taking effect on 15 November, both after the meeting).
   - The known `xfail` test `test_backend_drops_events_outside_window` covers this. The backend should drop or flag events whose date is not inside `research_window`.
2. **Undated events and wrong statements about the sources.**
   - About half of the events (37 of 71) have no date, often when the source gives one.
   - Descriptions often say "the sources give no date" when they do. This is the main reason for low groundedness for Kellogg's (0) and Plata (0.4).
   - A likely cause is that article text is cut to 1,500 characters in the prompt.
3. **Wrong person with the same name.** In test_002 the briefing about Sam Altman (Disney marketing director) again includes OpenAI news. Over three evaluations this happened in every run that found sources for him (8 of 10 runs; the other 2 found nothing). The prompt should say to drop sources about a different person with the same name.
4. **The summary misses or contradicts the sources.** The summary is supported in only 63% of cases.
   - IKEA: the summary says there are "no partnerships" while a Best Buy shop-in-shop pilot is in the sources.
   - Michelin: one event out of six sources, and the Q3 sales report of 20 October is missing.
5. **No retry on connection drops.** `anthropic_client` streams without retrying, so in the first pass 5 cases failed with `APITimeoutError` after 40–60 seconds.

### Research (Irina)

6. **Scientists are not found.** Gordeliy, Gelfand and one Sam Altman case get zero sources even with a working network. Tavily news search does not find publications or institute pages, so a recent Nature Communications paper and the 2026 Nobel Prize for channelrhodopsins are both missed. Research meetings need a different source (Google Scholar, PubMed, lab pages), or a wider window for background.
7. **"Nothing found" cannot be told apart from a failed search.** During the network problems, Tavily returned zero usable results for IKEA and Plata. On a rerun it returned 9 and 8. In both situations the user sees "The search found no usable sources", which reads as "there is no news". A retry on zero results, and a different message when the search itself fails, would help.

### Evaluation (Diliara)

8. **Running each case once is not enough.** In an earlier run with 3 repeats, `overall_score` for OpenAI ranged from 2 to 4. Use `--runs 3` when comparing backend versions.
9. **Many source sites block automatic page fetches** (403 and 502 errors), so for those sources the judge falls back to the backend's text. The `sources_unreachable` column shows how often this happened.

## How to reproduce

```sh
git worktree add ../mb-integration integration   # or the branch with the backend
python testing/run_eval.py --repo ../mb-integration --backend briefing_generator --fetch-sources --judge
python testing/run_eval.py --repo ../mb-integration --backend briefing_generator --fetch-sources --judge --only jensen_huang   # one entity, all meetings
```

`.env` needs `TAVILY_API_KEY` and `ANTHROPIC_API_KEY`.
