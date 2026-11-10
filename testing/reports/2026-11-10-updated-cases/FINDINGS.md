# Rerun of the updated cases, 10 November 2026

This run covers the four cases whose expected facts changed in `dataset.json`: Plata, Bending Spoons, Kellogg's and Pop Mart. Full results are in [report.xlsx](report.xlsx) and [report.json](report.json). For the other cases, see [the full run of the same day](../2026-11-10/FINDINGS.md).

## Setup

The setup matches the full run:
- **Backend:** the local `integration` branch, commit `4c2d071`.
- **Models:** the agent runs on `claude-sonnet-5-5` and the judge on `claude-opus-5-5`.
- **Options:** `--fetch-sources --judge`, one run per case.
- **Cost:** $2.20.

Unit tests: **97 passed, 0 failed, 3 xfail**.

The first attempt for Plata and Kellogg's got zero sources from Tavily. Tested directly a few minutes later, the same search returned 6 and 9 sources three times in a row, so both cases were rerun. This report uses the rerun for those two cases. Tavily sometimes returns nothing, and the backend then reports "no usable sources" as if there were no news (see problem 7 in the full run).

## What changed in the dataset

- **New check:** a dated fact must be at most 30 days before the meeting. Older context has to be marked `"background": true`, or the meeting has to set `"allow_old_facts": true`.
- **Plata:** added the $1 billion ARR milestone (19 October). The source is a paid press release, marked `"paid": true`. The April Series C is now marked as background.
- **Bending Spoons:** all facts are now inside the window:
  - the $1.5 billion debt raise (27 October);
  - Berkshire completing a further $330 million investment (29 October);
  - the Miro acquisition completed (26 October);
  - the Q3 results date announced (6 November).
- **Kellogg's:** the 2025 acquisitions were replaced by the UK Rice Krispies and Coco Pops recipe change (BBC, 4 November). Mentioning "Keith Kellogg" fails the case. A National Post article about child labour was not added because it is from 2016.
- **Pop Mart:** added the Q3 revenue fall of 45–50% (CNBC; FT, paywalled, marked `"paywall": true`) and the share drop (27–28 October).

## Results

| Case | recall | grounded | dates | entity | in window | relevance | summary ok | overall | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| plata | 0.69 | 0.6 | 0.8 | 0.8 | 0.2 | 4.0 | no | 4 | ISSUES |
| pop_mart | 0.5 | 1.0 | 0.75 | 1.0 | 1.0 | 4.0 | no | 4 | ISSUES |
| kelloggs | 0.5 | 0.6 | 0.8 | 0.8 | 0.75 | 3.0 | no | 4 | ISSUES |
| bending_spoons | 0.64 | 0.6 | 1.0 | 1.0 | 0.4 | 4.0 | no | 4 | FAIL |

## Findings

- **The main news is found.** Each case covers its most important recent fact:
  - Plata's $1 billion ARR;
  - Pop Mart's Q3 revenue fall;
  - the Kellogg's recipe change (partial: the cause and the quieter "snap, crackle and pop" are missing);
  - Bending Spoons' debt raise.
- **No namesake mix-up for Kellogg's.** Keith Kellogg does not appear.
- **Events outside the window** (the known `xfail` backend gap):
  - Bending Spoons gets the July IPO, the September Miro agreement and an October 7 share jump. The agent even labels two of them "background, before the research window", but still lists them as events.
  - Only 1 of 5 Plata events is placed inside the window by its sources.
- **Out-of-date status.** The sources for Bending Spoons say both the Miro deal and the Berkshire investment were completed. The briefing calls the first "agreed, closing status unknown" and the second "not confirmed". Recall counts these facts as partial.
- **Wrong claims about the sources** (the same pattern as in the full run). Every summary has at least one unsupported claim, usually about what the sources contain:
  - "no date, terms or details" about Plata's valuation, while the source gives the funding round and its date;
  - "terms not stated" for the Bending Spoons debt raise, while Bloomberg gives them;
  - "nothing on Pop Mart's plans in Italy", while a source reports a flagship in Milan.
- **A competitor counted as an event.** Plata's briefing has an event about Revolut launching in Mexico, and Kellogg's has one about the food industry in general. The judge counts them as other entities. They may be useful context, but they should not be listed as the counterpart's events.
- **Pop Mart's Paris flagship (9 November) was not found.** The briefing mentions only the London Oxford Street opening.
