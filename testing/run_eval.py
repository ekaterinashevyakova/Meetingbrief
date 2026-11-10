"""Evaluation runner for the MeetingBrief research agent.

Runs every case in dataset.json through generate_briefing(), applies
deterministic contract checks, and optionally scores content with an
LLM judge: Claude through the Anthropic API, or any OpenAI-compatible
endpoint (e.g. the Bifrost gateway). Tracks time, tokens and estimated
cost per step. Each run writes report.json and report.xlsx to
results/<timestamp>/ and appends one row to results/history.xlsx.

Usage (from the repository root):

    python testing/run_eval.py                          # demo backend, checks only
    python testing/run_eval.py --backend backend:generate_briefing --runs 3
    python testing/run_eval.py --from-cache testing/results/<run>  # re-score saved outputs
    python testing/run_eval.py --judge                  # also run the LLM judge
    python testing/run_eval.py --repo ../mb-bifrost --backend briefing_generator
    python testing/run_eval.py --fetch-sources --judge  # verify against the real pages

--fetch-sources downloads every source URL itself, checks that the backend's
excerpt is really on the page, and gives the judge the page text instead of
the text the backend supplied. Pages are cached in <run dir>/pages/.

Judge configuration (environment variables or the repository's .env):
    JUDGE_PROVIDER     anthropic | openai (default: openai when JUDGE_BASE_URL
                       is set, otherwise anthropic)
    JUDGE_MODEL        default claude-opus-5-5 for anthropic; required for openai
    JUDGE_MAX_SOURCE_CHARS  characters of each source shown to the judge (20000)
  Anthropic:
    ANTHROPIC_API_KEY  required
    JUDGE_EFFORT       low | medium | high | xhigh | max (default high)
  OpenAI-compatible:
    JUDGE_BASE_URL     e.g. http://localhost:8080/v1
    JUDGE_API_KEY      optional, sent as a Bearer token
    JUDGE_AUTH_HEADER  optional full Authorization value (e.g. BIFROST_BASIC_AUTH)
"""

import argparse
import ast
import functools
import hashlib
import importlib
import json
import os
import re
import sys
import time
import traceback
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:  # optional: environment variables work without it
    load_dotenv = None

TESTING = Path(__file__).resolve().parent
# Repository whose app.py and backend are tested. Defaults to this checkout;
# --repo points it at another one (e.g. a git worktree of a teammate's branch).
ROOT = TESTING.parent
RESULTS = TESTING / "results"
HISTORY_FILE = RESULTS / "history.xlsx"

# The agent generates on Sonnet and is judged by Opus, a stronger model from
# outside the generation run. The runner sets ANTHROPIC_MODEL (read by the
# backend's anthropic_client) unless it is already set, and warns when a call
# ran on another model (e.g. after a server-side fallback).
AGENT_MODEL = "claude-sonnet-5-5"
JUDGE_MODEL = "claude-opus-5-5"

REQUEST_FIELDS = (
    "entity_type", "name", "details", "meeting_datetime", "timezone", "goal",
)
WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# Contract validation: reuse validate_result() from app.py without running
# the Streamlit UI (importing app.py would execute the whole page).
# ---------------------------------------------------------------------------

def load_app_validators(root):
    tree = ast.parse((root / "app.py").read_text(encoding="utf-8"))
    wanted = {"safe_url", "validate_result"}
    nodes = [
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in wanted
    ]
    missing = wanted - {n.name for n in nodes}
    if missing:
        raise RuntimeError(f"app.py no longer defines: {sorted(missing)}")
    namespace = {"urlparse": urlparse}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"),
         namespace)
    return namespace["safe_url"], namespace["validate_result"]


# The contract always comes from this checkout's app.py (the frontend that
# will display the result), even when the backend is imported from --repo.
safe_url, validate_result = load_app_validators(ROOT)


def use_repo(root):
    """Import the backend from the given repository."""
    global ROOT
    ROOT = Path(root).resolve()
    sys.path.insert(0, str(ROOT))


use_repo(ROOT)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

IMPORTANCE = {"high": 1.0, "medium": 0.6, "low": 0.3}
DEFAULT_IMPORTANCE = "medium"
# Per-meeting keys that may override the case's values.
MEETING_OVERRIDES = ("must_not_contain", "expected_gaps", "expect_no_events",
                     "description")


def fact_kind(fact_date, request):
    """recent: inside the window; background: before it or undated;
    future: after the meeting, so the briefing must not mention it."""
    if not fact_date:
        return "background"
    day = date.fromisoformat(fact_date)
    start, end = research_window(request)
    if day > end:
        return "future"
    return "recent" if day >= start else "background"


def _meetings(case):
    """[(meeting_datetime, overrides)] from 'meetings' or the request."""
    if "meetings" not in case:
        return [(case.get("request", {}).get("meeting_datetime"), {})]
    out = []
    for m in case["meetings"]:
        if isinstance(m, str):
            out.append((m, {}))
        elif isinstance(m, dict):
            out.append((m.get("datetime"),
                        {k: m[k] for k in MEETING_OVERRIDES if k in m}))
        else:
            out.append((None, {}))
    return out


def load_dataset(path):
    """Validate dataset.json and expand it into one case per meeting.

    A case gives its meeting as request.meeting_datetime or as a list in
    "meetings" (strings, or objects with "datetime" and overrides of
    must_not_contain, expected_gaps, expect_no_events, description). With
    several meetings, each becomes a case "<id>@<YYYY-MM-DD>". Each expected
    fact gets "kind" from its date and that meeting's window, and a numeric
    "importance" from high / medium / low.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    problems, cases, seen = [], [], set()
    for i, case in enumerate(raw):
        cid = case.get("id")
        label = cid or f"#{i}"
        if not cid:
            problems.append(f"{label}: missing id")
        elif cid in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(cid)
        req = case.get("request", {})
        if "meetings" in case and "meeting_datetime" in req:
            problems.append(f"{label}: give either meetings or "
                            "request.meeting_datetime, not both")
        if "meetings" in case and not (
                isinstance(case["meetings"], list) and case["meetings"]):
            problems.append(f"{label}: meetings must be a non-empty list")
        for field in REQUEST_FIELDS:
            if field != "meeting_datetime" and not isinstance(
                    req.get(field), str):
                problems.append(f"{label}: request.{field} must be a string")
        if req.get("entity_type") not in ("company", "person"):
            problems.append(f"{label}: entity_type must be company or person")
        if "gaps" in case:
            problems.append(f"{label}: use 'expected_gaps', not 'gaps'")
        for fact in case.get("expected_facts", []):
            if not isinstance(fact.get("fact"), str) or not fact["fact"].strip():
                problems.append(f"{label}: every expected fact needs 'fact'")
            if "kind" in fact:
                problems.append(f"{label}: remove 'kind' from facts; it is "
                                "computed from 'date' for each meeting")
            imp = fact.get("importance", DEFAULT_IMPORTANCE)
            if imp not in IMPORTANCE:
                problems.append(f"{label}: importance must be one of "
                                f"{', '.join(IMPORTANCE)}, not {imp!r}")
            if fact.get("date") is not None:
                try:
                    date.fromisoformat(fact["date"])
                except (TypeError, ValueError):
                    problems.append(f"{label}: fact date must be YYYY-MM-DD")

        meetings = _meetings(case)
        for when, overrides in meetings:
            try:
                if datetime.fromisoformat(when).tzinfo is None:
                    problems.append(f"{label}: meeting {when} needs a UTC "
                                    "offset")
                    continue
            except (TypeError, ValueError):
                problems.append(f"{label}: invalid meeting datetime {when!r}")
                continue
            merged = {**case, **overrides}
            gaps = merged.get("expected_gaps", [])
            if not isinstance(gaps, list) or not all(
                    isinstance(g, str) and g.strip() for g in gaps):
                problems.append(f"{label}: expected_gaps must be non-empty "
                                "strings")
            request = {**req, "meeting_datetime": when}
            out = {k: v for k, v in merged.items() if k != "meetings"}
            out["base_id"] = cid
            if len(meetings) > 1:
                out["id"] = f"{cid}@{when[:10]}"
            out["request"] = request
            out["expected_facts"] = [
                {**f, "importance_label": f.get("importance",
                                                DEFAULT_IMPORTANCE),
                 "importance": IMPORTANCE.get(
                     f.get("importance", DEFAULT_IMPORTANCE), 0),
                 "kind": fact_kind(f.get("date"), request)
                 if not problems else "background"}
                for f in case.get("expected_facts", [])]
            cases.append(out)
    ids = [c["id"] for c in cases]
    for dup in sorted({x for x in ids if ids.count(x) > 1}):
        problems.append(f"{dup}: two meetings on the same day")
    if problems:
        raise SystemExit("Dataset errors:\n  " + "\n  ".join(problems))
    return cases


# ---------------------------------------------------------------------------
# Deterministic checks. Each returns a list of failure messages.
# ---------------------------------------------------------------------------

def research_window(request):
    meeting = datetime.fromisoformat(request["meeting_datetime"])
    return (meeting - timedelta(days=WINDOW_DAYS)).date(), meeting.date()


def all_text(result):
    parts = [result.get("summary", "")]
    for e in result.get("events", []):
        parts += [e.get("title", ""), e.get("description", ""),
                  e.get("discussion_point", "") or ""]
    for key in ("questions", "conversation_starters", "gaps"):
        parts += result.get(key, [])
    for s in result.get("sources", []):
        parts += [s.get("title", ""), s.get("excerpt", "") or "",
                  s.get("url", "") or ""]
    return "\n".join(p for p in parts if isinstance(p, str))


def deterministic_checks(case, result):
    failures, warnings = [], []
    try:
        validate_result(result)
    except Exception as exc:  # noqa: BLE001 - report any validation error
        return [f"validate_result: {exc}"], warnings

    if result["is_demo"]:
        failures.append("is_demo is True (fictional content, not research)")

    start, end = research_window(case["request"])
    for e in result["events"]:
        d = e.get("date")
        if d is None:
            warnings.append(f"event without date: {e['title']!r}")
            continue
        try:
            parsed = date.fromisoformat(d)
            if len(d) != 10:
                raise ValueError
        except (TypeError, ValueError):
            failures.append(f"event date not YYYY-MM-DD: {d!r}")
            continue
        if not start <= parsed <= end:
            failures.append(
                f"event outside window {start}..{end}: {d} {e['title']!r}")
        if not e.get("source_ids"):
            failures.append(f"event without sources: {e['title']!r}")

    cited = {ref for e in result["events"] for ref in e.get("source_ids", [])}
    for s in result["sources"]:
        url = s.get("url")
        if url is not None and safe_url(url) is None:
            failures.append(f"non-http(s) source URL: {url!r}")
        if not s.get("excerpt"):
            warnings.append(f"source without excerpt: {s['id']}")
        if s["id"] not in cited:
            warnings.append(f"source never cited by an event: {s['id']}")
        # Optional backend field (Tavily estimate), not part of the UI contract.
        published = s.get("published_date")
        if published:
            try:
                pub = datetime.fromisoformat(published).date()
                if not start <= pub <= end:
                    warnings.append(
                        f"source published outside window: {s['id']} {pub}")
            except (TypeError, ValueError):
                warnings.append(f"invalid published_date: {s['id']}")

    if "researched_at" in result:
        try:
            ts = datetime.fromisoformat(result["researched_at"])
            if ts.tzinfo is None:
                failures.append("researched_at has no UTC offset")
        except (TypeError, ValueError):
            failures.append(f"invalid researched_at: {result['researched_at']!r}")
    else:
        warnings.append("researched_at not provided")

    text = all_text(result).lower()
    for term in case.get("must_not_contain", []):
        if term.lower() in text:
            failures.append(f"contains forbidden term (wrong entity?): {term!r}")

    if case.get("expect_no_events") and result["events"]:
        failures.append(f"expected no events, got {len(result['events'])}")

    failures += gap_checks(result, warnings)
    return failures, warnings


def normalize(text):
    return " ".join(text.lower().split()).rstrip(".!? ")


def gap_checks(result, warnings):
    """gaps: no blanks, no repeats, and an explanation when nothing was found."""
    failures = []
    gaps = result["gaps"]
    if any(not g.strip() for g in gaps):
        failures.append("empty string in gaps")
    seen = set()
    for g in gaps:
        key = normalize(g)
        if key and key in seen:
            warnings.append(f"duplicate gap: {g.strip()!r}")
        seen.add(key)
    if not result["events"] and not any(g.strip() for g in gaps):
        failures.append("no events and no gaps explaining why")
    return failures


# ---------------------------------------------------------------------------
# Independent source check (--fetch-sources). The backend's excerpt and
# article_text could be wrong or invented, so fetch each URL ourselves,
# check the excerpt is on the page, and judge events against the page.
# ---------------------------------------------------------------------------

FETCH_TIMEOUT = 15
FETCH_MAX_BYTES = 3_000_000
MIN_PAGE_CHARS = 300      # less readable text than this: paywall, JS-only page
EXCERPT_MATCH_MIN = 0.6   # share of the excerpt's 5-word sequences on the page
SHINGLE = 5
USER_AGENT = "Mozilla/5.0 (compatible; MeetingBrief-eval/1.0)"


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
             "tr", "section", "article", "header", "footer", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)


def _download(url):
    """(body bytes, content type, charset). Raises on HTTP or network errors."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        body = resp.read(FETCH_MAX_BYTES)
        return (body, resp.headers.get_content_type(),
                resp.headers.get_content_charset())


def fetch_page(url, cache_dir):
    """Fetch url once per cache_dir. Status: ok, unreadable or unavailable."""
    cache = Path(cache_dir) / (
        hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".json")
    if cache.exists():
        return {**json.loads(cache.read_text(encoding="utf-8")),
                "cached": True}
    started = time.perf_counter()
    page = {"url": url, "status": "ok", "error": None, "text": ""}
    try:
        body, ctype, charset = _download(url)
        text = body.decode(charset or "utf-8", errors="replace")
        if ctype in ("text/html", "application/xhtml+xml"):
            text = html_to_text(text)
        elif not ctype.startswith("text/"):
            page.update(status="unreadable", error=f"content type {ctype}")
            text = ""
        page["text"] = text
        if page["status"] == "ok" and len(text) < MIN_PAGE_CHARS:
            page.update(status="unreadable",
                        error=f"only {len(text)} characters of text")
    except Exception as exc:  # noqa: BLE001 - any fetch error is reported
        page.update(status="unavailable", error=f"{type(exc).__name__}: {exc}")
    page["seconds"] = round(time.perf_counter() - started, 2)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    return {**page, "cached": False}


def _words(text):
    """Lowercase word tokens with their start offsets in text."""
    return [(m.group().lower(), m.start())
            for m in re.finditer(r"\w+", text or "")]


def excerpt_match(excerpt, text):
    """(share of the excerpt's word sequences found in text, offset or None).

    Word-level, so punctuation, quotes and whitespace differences between
    the excerpt and the page do not matter.
    """
    ex = [w for w, _ in _words(excerpt)]
    if not ex:
        return None, None
    page = _words(text)
    n = min(SHINGLE, len(ex))
    index = {}
    for i in range(len(page) - n + 1):
        index.setdefault(tuple(w for w, _ in page[i:i + n]), page[i][1])
    grams = [tuple(ex[i:i + n]) for i in range(len(ex) - n + 1)]
    hits = [index[g] for g in grams if g in index]
    return round(len(hits) / len(grams), 2), (hits[0] if hits else None)


def verify_sources(result, cache_dir):
    """One record per source, plus {url: page} for the judge."""
    records, pages = [], {}
    for s in result["sources"]:
        url = s.get("url")
        rec = {"source_id": s["id"], "url": url, "status": "no_url",
               "error": None, "excerpt_match": None, "excerpt_found": None,
               "seconds": 0, "cached": False}
        if url and safe_url(url):
            page = fetch_page(url, cache_dir)
            rec.update(status=page["status"], error=page["error"],
                       seconds=page.get("seconds") or 0,
                       cached=page.get("cached", False))
            if page["status"] == "ok":
                score, offset = excerpt_match(s.get("excerpt"), page["text"])
                rec["excerpt_match"] = score
                if score is not None:
                    rec["excerpt_found"] = score >= EXCERPT_MATCH_MIN
                pages[url] = {"text": page["text"], "offset": offset}
        records.append(rec)
    return records, pages


def source_warnings(records):
    out = []
    for r in records:
        if r["status"] == "unavailable":
            out.append(f"source page unavailable: {r['source_id']} "
                       f"({r['error']})")
        elif r["status"] == "unreadable":
            out.append(f"source page has no readable text: {r['source_id']} "
                       f"({r['error']})")
        elif r["excerpt_found"] is False:
            out.append(f"excerpt not found on source page: {r['source_id']} "
                       f"(match {r['excerpt_match']})")
    return out


def source_metrics(records):
    out = {"sources_unreachable": sum(
        r["status"] in ("unavailable", "unreadable") for r in records)}
    checked = [r["excerpt_found"] for r in records
               if isinstance(r["excerpt_found"], bool)]
    if checked:
        out["excerpt_verified"] = _share(checked)
    return out


def evidence_window(text, offset, limit):
    """At most limit characters of text, starting a little before offset."""
    if len(text) <= limit:
        return text
    start = 0 if offset is None else max(
        0, min(offset - limit // 4, len(text) - limit))
    return text[start:start + limit]


# ---------------------------------------------------------------------------
# Scores. One entry per key of summarize_scores() and source_metrics(); the
# same list fills the Criteria sheet of report.xlsx.
# ---------------------------------------------------------------------------

CRITERIA = [
    ("recall_all", "Expected facts the briefing covers, weighted by "
     "importance (high 1.0, medium 0.6, low 0.3); a partial match counts "
     "half. Facts dated after the meeting are excluded.", "0-1", "higher"),
    ("recall_recent", "Same, only facts inside the 30-day window.",
     "0-1", "higher"),
    ("recall_background", "Same, only older or undated background facts.",
     "0-1", "higher"),
    ("contradicted", "Expected facts the briefing contradicts.",
     "count", "lower"),
    ("future_leaks", "Facts dated after the meeting that the briefing "
     "mentions anyway (information the attendee could not have had). Any "
     "leak fails the run.", "count", "lower"),
    ("groundedness", "Events whose every statement is supported by the "
     "cited sources.", "0-1", "higher"),
    ("date_accuracy", "Events whose date is the date of the event itself "
     "according to the sources, not a publication date. A null date counts "
     "when the sources give none.", "0-1", "higher"),
    ("entity_precision", "Events about the requested company or person, "
     "not a namesake. Subsidiaries and brands count.", "0-1", "higher"),
    ("in_window_rate", "Events the sources place inside the research "
     "window, among events whose timing the sources establish.",
     "0-1", "higher"),
    ("in_window_unknown", "Events whose timing the sources do not "
     "establish.", "count", "lower"),
    ("event_relevance", "Mean relevance of events to the meeting goal "
     "(5: directly matters for the goal, 3: useful background, 1: "
     "unrelated).", "1-5", "higher"),
    ("relevant_event_rate", "Events with relevance 3 or higher.",
     "0-1", "higher"),
    ("excerpt_verified", "--fetch-sources: fetched sources whose excerpt "
     "is really on the page.", "0-1", "higher"),
    ("sources_unreachable", "--fetch-sources: source pages that could not "
     "be fetched or had no readable text, so the judge saw the backend's "
     "text instead.", "count", "lower"),
    ("summary_supported", "The summary has no claims the sources do not "
     "support.", "true/false", "true"),
    ("summary_unsupported", "Summary claims the sources do not support.",
     "count", "lower"),
    ("gap_recall", "Expected limitations (expected_gaps) that the briefing "
     "mentions.", "0-1", "higher"),
    ("vague_gaps", "Generic gap items such as 'some information may be "
     "missing'.", "count", "lower"),
    ("non_limitation_gaps", "Gap items that state a fact or a guess instead "
     "of a limitation.", "count", "lower"),
    ("goal_score", "Usefulness and specificity of questions and discussion "
     "points for the meeting goal.", "1-5", "higher"),
    ("duplicate_events", "Events that repeat another event in different "
     "words.", "count", "lower"),
    ("overall_score", "How well the briefing prepares someone for this "
     "meeting.", "1-5", "higher"),
]
SCORE_KEYS = [key for key, *_ in CRITERIA]

FACT_VERDICTS = {"covered", "partial", "not_covered", "contradicted"}
# Weight of each verdict in recall: a partial match counts half.
VERDICT_CREDIT = {"covered": 1.0, "partial": 0.5}

MATCH_RULES = (
    "Judge meaning, not wording. Count a fact as covered when the briefing "
    "conveys its main point, even if it is paraphrased, shorter, less "
    "detailed, uses rounded or approximate numbers, spreads it over several "
    "sentences, or mentions it only in a question, a discussion point or a "
    "limitation. Use partial when the briefing touches the same development "
    "but misses or blurs its key point (for example, it mentions an "
    "investigation but not what it is about). Use contradicted only when the "
    "briefing states something incompatible with the fact. Do not require "
    "exact figures, source names or dates unless they are the point of the "
    "fact.")


def max_source_chars():
    return int(os.environ.get("JUDGE_MAX_SOURCE_CHARS", "20000"))


# ---------------------------------------------------------------------------
# Token usage and cost
# ---------------------------------------------------------------------------

# Estimated USD per 1M tokens: (input, output, cache read). Cache writes are
# billed at 1.25x input. Anthropic list prices as of October 2026; check
# current prices before relying on the cost columns. Unknown models get no
# cost estimate.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
}
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "thinking_tokens")


def estimate_cost(call):
    price = PRICES.get(call.get("model") or "")
    if price is None:
        return None
    inp, out, cache_read = price
    return round((
        (call.get("input_tokens") or 0) * inp
        + (call.get("cache_write_tokens") or 0) * inp * 1.25
        + (call.get("cache_read_tokens") or 0) * cache_read
        + (call.get("output_tokens") or 0) * out
    ) / 1_000_000, 6)


def token_counts(usage):
    """Token counts from an Anthropic usage object.

    With a server-side fallback, top-level usage covers only the final
    attempt; usage.iterations lists every attempt, so sum those.
    """
    parts = getattr(usage, "iterations", None) or [usage]

    def total(field):
        return sum(getattr(p, field, 0) or 0 for p in parts)

    details = getattr(usage, "output_tokens_details", None)
    return {
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "cache_read_tokens": total("cache_read_input_tokens"),
        "cache_write_tokens": total("cache_creation_input_tokens"),
        "thinking_tokens": getattr(details, "thinking_tokens", None),
    }


def usage_totals(calls):
    """Sum seconds, tokens and estimated cost over usage records."""
    total = {"calls": len(calls), "seconds": 0.0, "cost_usd": 0.0,
             **{field: 0 for field in TOKEN_FIELDS}}
    for call in calls:
        total["seconds"] += call.get("seconds") or 0
        for field in TOKEN_FIELDS:
            total[field] += call.get(field) or 0
        total["cost_usd"] += estimate_cost(call) or 0
    total["seconds"] = round(total["seconds"], 1)
    total["cost_usd"] = round(total["cost_usd"], 4)
    return total


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = ("You are a strict evaluator. Answer with a single JSON "
                "object and nothing else.")


def parse_json_object(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError(f"judge reply has no JSON object: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def _who(request):
    name = request.get("name")
    if not name:
        return "the requested entity"
    who = f"{request.get('entity_type') or 'entity'} \"{name}\""
    if request.get("details"):
        who += f" ({request['details']})"
    return who


def _bullets(items):
    return "\n".join(f"- {item}" for item in items) or "(none)"


class Judge:
    """Criteria prompts shared by all judge providers.

    Subclasses implement _complete(prompt) -> (text, usage dict). ask()
    records every call in self.calls with its criterion, tokens and seconds.
    """

    provider = ""
    model = ""
    effort = ""

    def ask(self, prompt):
        started = time.perf_counter()
        text, usage = self._complete(prompt)
        self.__dict__.setdefault("calls", []).append({
            "stage": f"judge:{getattr(self, '_label', 'other')}",
            **usage,
            "model": usage.get("model") or self.model,
            "seconds": round(time.perf_counter() - started, 2),
        })
        return parse_json_object(text)

    def _complete(self, prompt):
        raise NotImplementedError

    def _ask(self, label, prompt):
        self._label = label
        return self.ask(prompt)

    def facts_coverage(self, briefing_text, facts):
        """One verdict per expected fact, in the order given."""
        reply = self._ask(
            "facts",
            "Briefing:\n<<<\n" + briefing_text + "\n>>>\n\n"
            "Expected facts:\n"
            + "\n".join(f"{i}. {fact}" for i, fact in enumerate(facts, 1))
            + "\n\nFor every expected fact, decide whether the briefing "
            "covers it, covers it partially, does not mention it, or "
            "contradicts it. " + MATCH_RULES + " Reply {\"items\": "
            "[{\"n\": 1, \"verdict\": \"covered\" | \"partial\" | "
            "\"not_covered\" | \"contradicted\", \"reason\": \"...\"}]}."
        )
        by_n = {}
        for item in reply.get("items") or []:
            try:
                by_n[int(item["n"])] = item
            except (KeyError, TypeError, ValueError):
                continue
        verdicts = []
        for n in range(1, len(facts) + 1):
            item = by_n.get(n, {})
            if item.get("verdict") in FACT_VERDICTS:
                verdicts.append({"verdict": item["verdict"],
                                 "reason": item.get("reason", "")})
            else:
                verdicts.append({"verdict": "not_covered",
                                 "reason": "judge gave no valid verdict",
                                 "judge_missing": True})
        return verdicts

    def event_support(self, event, excerpts, request=None):
        request = request or {}
        context = [f"Requested: {_who(request)}",
                   f"Meeting goal: {request.get('goal') or '(not specified)'}"]
        if request.get("meeting_datetime"):
            start, end = research_window(request)
            context.append(f"Research window: {start} to {end} "
                           f"(meeting {request['meeting_datetime']})")
        return self._ask(
            "event",
            "\n".join(context) + "\n\n"
            f"Event title: {event['title']}\n"
            f"Event date: {event.get('date') or 'null (not established)'}\n"
            f"Event description: {event['description']}\n\n"
            "Cited source texts:\n<<<\n" + "\n---\n".join(excerpts)
            + "\n>>>\n\nAnswer from the cited sources only.\n"
            "- supported: is every factual statement in the event title and "
            "description supported?\n"
            "- date_ok: if a date is given, do the sources show that the "
            "event itself happened on that date, not only that an article "
            "was published then? If the date is null, answer true when the "
            "sources do not establish the event date and false when they "
            "clearly do.\n"
            "- about_entity: is the event about the requested company or "
            "person, not a different one with a similar name? A subsidiary, "
            "brand or franchise of the requested company counts.\n"
            "- in_window: did the event happen inside the research window? "
            "Answer \"unknown\" if the sources do not say when it happened.\n"
            "- goal_relevance: from 1 to 5, how much this event matters for "
            "a meeting with this goal. 5: it directly affects the goal or "
            "what should be discussed; 3: useful background on the "
            "counterpart; 1: unrelated to the goal. Rate relevance only, "
            "not truthfulness. If no goal is given, rate relevance for a "
            "general business meeting.\n"
            "Reply {\"supported\": true | false, \"date_ok\": true | false, "
            "\"about_entity\": true | false, \"in_window\": true | false | "
            "\"unknown\", \"goal_relevance\": 1-5, \"reason\": \"...\"}."
        )

    def summary_support(self, summary, evidence):
        return self._ask(
            "summary",
            f"Summary: {summary}\n\nSources:\n<<<\n"
            + ("\n---\n".join(evidence) or "(no sources)")
            + "\n>>>\n\nList every factual statement in the summary that the "
            "sources do not support. Statements about the research itself "
            "(for example, that nothing relevant was found) need no support. "
            "Reply {\"supported\": true | false, "
            "\"unsupported_claims\": [\"...\"]}."
        )

    def gap_coverage(self, gaps, expected_gap):
        return self._ask(
            "expected_gap",
            "Limitations listed in a briefing:\n- "
            + "\n- ".join(gaps or ["(none)"])
            + f"\n\nExpected limitation: {expected_gap}\n\n"
            "Is this limitation mentioned? Judge meaning, not wording: it is "
            "covered if any item says the same thing in other words, more "
            "generally or as part of a longer item; partial if an item "
            "points in the same direction but misses the main point. "
            "Reply {\"verdict\": \"covered\" | \"partial\" | "
            "\"not_covered\", \"reason\": \"...\"}."
        )

    def gap_quality(self, goal, gaps):
        return self._ask(
            "gap_quality",
            f"Meeting goal: {goal or '(not specified)'}\n\n"
            "Each item below should describe a limitation of the research "
            "(what is missing or uncertain, and ideally why), not state a "
            "fact about the company or person.\n\nItems:\n"
            + "\n".join(f"{i}. {g}" for i, g in enumerate(gaps, 1))
            + "\n\nFor every item reply with: \"specific\" (true if it names "
            "what exactly is missing; false for generic text like 'some "
            "information may be missing') and \"is_limitation\" (false if it "
            "is actually a factual claim or a guess). Reply {\"items\": "
            "[{\"n\": 1, \"specific\": true, \"is_limitation\": true, "
            "\"reason\": \"...\"}]}."
        )

    def goal_relevance(self, goal, result):
        items = result["questions"] + [
            e.get("discussion_point") or "" for e in result["events"]
        ]
        return self._ask(
            "goal",
            f"Meeting goal: {goal or '(not specified)'}\n\n"
            "Suggested questions and discussion points:\n- "
            + "\n- ".join(i for i in items if i)
            + "\n\nRate from 1 to 5 how useful and specific these are for "
            "this goal. Reply {\"score\": 1-5, \"reason\": \"...\"}."
        )

    def briefing_review(self, request, result):
        events = "\n".join(
            f"{i}. [{e.get('date') or 'no date'}] {e['title']}: "
            f"{e['description']}"
            for i, e in enumerate(result["events"], 1)) or "(none)"
        return self._ask(
            "review",
            f"Meeting with {_who(request)}. "
            f"Goal: {request.get('goal') or '(not specified)'}\n\n"
            f"Briefing summary: {result['summary']}\n\n"
            f"Events:\n{events}\n\n"
            f"Questions:\n{_bullets(result['questions'])}\n\n"
            "Conversation starters:\n"
            f"{_bullets(result['conversation_starters'])}\n\n"
            f"Limitations:\n{_bullets(result['gaps'])}\n\n"
            "1. duplicates: list pairs of event numbers that describe the "
            "same real-world development.\n"
            "2. overall_score: rate from 1 to 5 how well this briefing "
            "prepares someone for this meeting (5: accurate, specific, covers "
            "what matters for the goal; 1: useless or misleading). Do not "
            "reward length. If nothing relevant could be found, an honest "
            "briefing that says so and names what is missing can score well; "
            "invented content scores 1.\n"
            "Reply {\"duplicates\": [] or [[i, j], ...], "
            "\"overall_score\": 1-5, \"reason\": \"...\"}."
        )


class OpenAICompatJudge(Judge):
    """Any OpenAI-compatible /chat/completions endpoint, e.g. Bifrost."""

    provider = "openai"

    def __init__(self):
        self.base = os.environ.get("JUDGE_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("JUDGE_MODEL", "")
        self.key = os.environ.get("JUDGE_API_KEY", "")
        # Full Authorization header value, e.g. the same one as
        # BIFROST_BASIC_AUTH ("Basic ..."). Takes precedence over the key.
        self.auth = os.environ.get("JUDGE_AUTH_HEADER", "")
        if not self.base or not self.model:
            raise SystemExit("--judge with JUDGE_PROVIDER=openai needs "
                             "JUDGE_BASE_URL and JUDGE_MODEL")
        self.calls = []

    def _complete(self, prompt):
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        }).encode()
        headers = {"Content-Type": "application/json"}
        if self.auth:
            headers["Authorization"] = self.auth
        elif self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
        usage = data.get("usage") or {}
        return data["choices"][0]["message"]["content"], {
            "model": data.get("model") or self.model,
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
        }


class AnthropicJudge(Judge):
    """Claude through the official Anthropic SDK."""

    provider = "anthropic"

    def __init__(self):
        try:
            import anthropic
        except ImportError:
            raise SystemExit("--judge needs the anthropic package: "
                             "pip install -r testing/requirements.txt")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("--judge needs ANTHROPIC_API_KEY (or "
                             "JUDGE_BASE_URL for an OpenAI-compatible judge)")
        self.model = os.environ.get("JUDGE_MODEL") or JUDGE_MODEL
        self.effort = os.environ.get("JUDGE_EFFORT") or "high"
        self.client = anthropic.Anthropic()
        self.calls = []

    def _complete(self, prompt):
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=JUDGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.effort},
            # On a safety decline, the API re-runs the request on a
            # recommended fallback model instead of returning a refusal.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("judge declined the request")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("judge reply was cut off at max_tokens")
        text = "".join(b.text for b in response.content if b.type == "text")
        return text, {"model": response.model,
                      **token_counts(response.usage)}


def make_judge():
    provider = os.environ.get("JUDGE_PROVIDER", "").strip().lower()
    if not provider:
        provider = "openai" if os.environ.get("JUDGE_BASE_URL") else "anthropic"
    if provider == "openai":
        return OpenAICompatJudge()
    if provider == "anthropic":
        return AnthropicJudge()
    raise SystemExit(f"JUDGE_PROVIDER must be anthropic or openai, "
                     f"not {provider!r}")


def judge_case(judge, case, result, pages=None):
    """pages: {url: {"text", "offset"}} from verify_sources(), optional."""
    request = case["request"]
    pages = pages or {}
    scores = {"facts": [], "events": [], "goal": None}

    facts = case.get("expected_facts", [])
    if facts:
        verdicts = judge.facts_coverage(all_text(result),
                                        [f["fact"] for f in facts])
        scores["facts"] = [{**f, **v} for f, v in zip(facts, verdicts)]

    # Evidence, capped to keep judge cost predictable: the page we fetched
    # ourselves when there is one (centred on the excerpt), otherwise the
    # backend's article_text, otherwise its excerpt.
    limit = max_source_chars()
    evidence, kind = {}, {}
    for s in result["sources"]:
        page = pages.get(s.get("url"))
        if page:
            evidence[s["id"]] = evidence_window(page["text"], page["offset"],
                                                limit)
            kind[s["id"]] = "page"
        else:
            evidence[s["id"]] = (s.get("article_text") or s.get("excerpt")
                                 or "")[:limit]
            kind[s["id"]] = "backend"
    for e in result["events"]:
        v = judge.event_support(
            e, [evidence[r] for r in e["source_ids"]], request)
        kinds = {kind[r] for r in e["source_ids"]}
        scores["events"].append({
            "title": e["title"], "date": e.get("date"), **v,
            "evidence": kinds.pop() if len(kinds) == 1 else "mixed"})

    scores["summary"] = judge.summary_support(
        result["summary"], list(evidence.values()))
    if result["questions"] or any(
            e.get("discussion_point") for e in result["events"]):
        scores["goal"] = judge.goal_relevance(request["goal"], result)
    scores["expected_gaps"] = [
        {"gap": g, **judge.gap_coverage(result["gaps"], g)}
        for g in case.get("expected_gaps", [])
    ]
    if result["gaps"]:
        quality = judge.gap_quality(
            request["goal"], result["gaps"]).get("items", [])
        for item in quality:
            n = item.get("n")
            if isinstance(n, int) and 1 <= n <= len(result["gaps"]):
                item.setdefault("gap", result["gaps"][n - 1])
        scores["gap_quality"] = quality
    scores["review"] = judge.briefing_review(request, result)
    return scores


def count_duplicates(pairs, n_events):
    """Events that repeat another one: group size minus one, per group."""
    parent = list(range(n_events + 1))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for pair in pairs or []:
        try:
            a, b = (int(x) for x in pair)
        except (TypeError, ValueError):
            continue
        if a != b and 1 <= a <= n_events and 1 <= b <= n_events:
            parent[find(a)] = find(b)
    return n_events - len({find(i) for i in range(1, n_events + 1)})


def _share(values):
    return round(sum(values) / len(values), 2) if values else None


def leak_failures(scores):
    """Failures for facts dated after the meeting that the briefing uses."""
    return [f"mentions a fact from after the meeting ({f.get('date')}): "
            f"{_short(f['fact'], 100)}"
            for f in scores.get("facts", [])
            if f.get("kind") == "future" and f.get("verdict") in VERDICT_CREDIT]


def summarize_scores(scores):
    out = {}
    facts = scores["facts"]
    known = [f for f in facts if f.get("kind") != "future"]
    for kind in ("all", "recent", "background"):
        subset = [f for f in known if kind == "all" or f["kind"] == kind]
        total = sum(f["importance"] for f in subset)
        if total:
            got = sum(f["importance"] * VERDICT_CREDIT.get(f.get("verdict"), 0)
                      for f in subset)
            out[f"recall_{kind}"] = round(got / total, 2)
    out["contradicted"] = sum(f.get("verdict") == "contradicted"
                              for f in known)
    future = [f for f in facts if f.get("kind") == "future"]
    if future:
        out["future_leaks"] = sum(f.get("verdict") in VERDICT_CREDIT
                                  for f in future)

    events = scores["events"]
    if events:
        ok = sum(bool(e.get("supported")) for e in events)
        out["groundedness"] = round(ok / len(events), 2)
    for field, key in (("date_ok", "date_accuracy"),
                       ("about_entity", "entity_precision")):
        share = _share([e[field] for e in events
                        if isinstance(e.get(field), bool)])
        if share is not None:
            out[key] = share
    timing = [e.get("in_window") for e in events if "in_window" in e]
    if timing:
        known = [t for t in timing if isinstance(t, bool)]
        if known:
            out["in_window_rate"] = _share(known)
        out["in_window_unknown"] = len(timing) - len(known)
    relevance = [e["goal_relevance"] for e in events
                 if isinstance(e.get("goal_relevance"), (int, float))
                 and not isinstance(e["goal_relevance"], bool)]
    if relevance:
        out["event_relevance"] = round(sum(relevance) / len(relevance), 2)
        out["relevant_event_rate"] = _share([r >= 3 for r in relevance])

    if scores.get("summary"):
        out["summary_supported"] = bool(scores["summary"].get("supported"))
        out["summary_unsupported"] = len(
            scores["summary"].get("unsupported_claims") or [])
    if scores["goal"]:
        out["goal_score"] = scores["goal"].get("score")
    expected = scores.get("expected_gaps", [])
    if expected:
        got = sum(VERDICT_CREDIT.get(g.get("verdict"), 0) for g in expected)
        out["gap_recall"] = round(got / len(expected), 2)
    quality = scores.get("gap_quality", [])
    if quality:
        out["vague_gaps"] = sum(not q.get("specific", True) for q in quality)
        out["non_limitation_gaps"] = sum(
            not q.get("is_limitation", True) for q in quality)

    review = scores.get("review")
    if review:
        out["duplicate_events"] = count_duplicates(
            review.get("duplicates"), len(events))
        if isinstance(review.get("overall_score"), (int, float)):
            out["overall_score"] = review["overall_score"]
    return out


# ---------------------------------------------------------------------------
# Backend timing and token usage
# ---------------------------------------------------------------------------

# Backend LLM clients that expose pop_usage() -> [{"model", tokens...}].
USAGE_MODULES = ("anthropic_client", "bifrost_client")


class BackendProbe:
    """Times the backend's search and LLM steps and collects token usage.

    Wraps search_sources() and generate_text() in the backend module, if it
    has them, and reads token usage from the LLM client's pop_usage().
    """

    STEPS = (("search_sources", "tavily"), ("generate_text", "generation"))

    def __init__(self, module):
        self.calls = []
        for name, stage in self.STEPS:
            func = getattr(module, name, None)
            if callable(func):
                setattr(module, name, self._timed(func, stage))

    def _timed(self, func, stage):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                self.calls.append({
                    "stage": stage,
                    "seconds": round(time.perf_counter() - started, 2)})
        return wrapper

    def collect(self):
        """Return and reset the calls since the last collect()."""
        calls, self.calls = self.calls, []
        usage = []
        for name in USAGE_MODULES:
            pop = getattr(sys.modules.get(name), "pop_usage", None)
            if callable(pop):
                usage += pop()
        generation = [c for c in calls if c["stage"] == "generation"]
        for call, tokens in zip(generation, usage):
            call.update({k: v for k, v in tokens.items() if k != "seconds"})
        # Usage without a timed call (the client was called another way).
        calls += [{"stage": "generation", **tokens}
                  for tokens in usage[len(generation):]]
        return calls


def entry_usage(entry):
    """Per-run time, tokens and cost for the Summary sheet and history."""
    usage = entry.get("usage") or {}
    backend = usage.get("backend") or []
    tavily = usage_totals([c for c in backend if c.get("stage") == "tavily"])
    gen = usage_totals([c for c in backend if c.get("stage") == "generation"])
    judge = usage_totals(usage.get("judge") or [])
    fetch = usage_totals(usage.get("fetch") or [])
    return {
        "backend_s": entry.get("seconds"),
        "tavily_s": tavily["seconds"],
        "tavily_searches": tavily["calls"],
        "llm_s": gen["seconds"],
        "fetch_s": fetch["seconds"],
        "judge_s": judge["seconds"],
        "gen_input_tokens": gen["input_tokens"],
        "gen_output_tokens": gen["output_tokens"],
        "judge_input_tokens": judge["input_tokens"],
        "judge_output_tokens": judge["output_tokens"],
        "gen_cost_usd": gen["cost_usd"],
        "judge_cost_usd": judge["cost_usd"],
        "total_cost_usd": round(gen["cost_usd"] + judge["cost_usd"], 4),
    }


USAGE_COLUMNS = list(entry_usage({}).keys())


def mean_scores(report):
    means = {}
    for key in SCORE_KEYS:
        values = [e["scores"][key] for e in report
                  if isinstance((e.get("scores") or {}).get(key), (int, float))]
        means[key] = round(sum(values) / len(values), 2) if values else None
    return means


def build_run_info(args, judge, report, started_at, wall_seconds, out_dir,
                   unit=None):
    totals = {key: 0 for key in USAGE_COLUMNS}
    for entry in report:
        for key, value in entry_usage(entry).items():
            totals[key] += value or 0
    gen_models = {c.get("model") for e in report
                  for c in (e.get("usage") or {}).get("backend", [])
                  if c.get("stage") == "generation" and c.get("model")}
    return {
        "run_id": out_dir.name,
        "started_at": started_at.isoformat(timespec="seconds"),
        "mode": "from-cache" if args.from_cache else "fresh",
        "backend": "(cached)" if args.from_cache else args.backend,
        "repo": str(ROOT),
        "dataset": str(args.dataset),
        "cases": len({e["id"] for e in report}),
        "runs": len(report),
        "passed": sum(not e["failures"] for e in report),
        "overview": run_overview_text(report, unit),
        **{f"unit_{k}": (unit["totals"][k] if unit else None)
           for k in UNIT_OUTCOMES},
        "unit_s": unit["seconds"] if unit else None,
        "runs_per_case": max((e["run"] for e in report), default=0),
        "agent_model_expected": os.environ.get("ANTHROPIC_MODEL", ""),
        "generation_models": ", ".join(sorted(gen_models)),
        "judge_provider": judge.provider if judge else "",
        "judge_model": judge.model if judge else "",
        "judge_effort": judge.effort if judge else "",
        "judge_max_source_chars": max_source_chars() if judge else None,
        "judge_errors": sum(bool(e.get("judge_error")) for e in report),
        "wall_s": round(wall_seconds, 1),
        **{key: round(value, 4) if isinstance(value, float) else value
           for key, value in totals.items()},
        "report_dir": str(out_dir),
    }


def print_totals(info):
    print(f"\nTime: wall {info['wall_s']}s · backend {info['backend_s']}s "
          f"(Tavily {info['tavily_s']}s in {info['tavily_searches']} "
          f"searches, LLM {info['llm_s']}s) · source fetch "
          f"{info['fetch_s']}s · judge {info['judge_s']}s")
    print(f"Tokens: generation {info['gen_input_tokens']:,} in / "
          f"{info['gen_output_tokens']:,} out · judge "
          f"{info['judge_input_tokens']:,} in / "
          f"{info['judge_output_tokens']:,} out")
    print(f"Estimated cost: generation ${info['gen_cost_usd']:.2f} · "
          f"judge ${info['judge_cost_usd']:.2f} · "
          f"total ${info['total_cost_usd']:.2f}")


# ---------------------------------------------------------------------------
# Unit tests (app_test.py) run as part of every evaluation
# ---------------------------------------------------------------------------

UNIT_OUTCOMES = ("passed", "failed", "error", "skipped", "xfailed")


def run_unit_tests(out_dir, repo):
    """Run app_test.py with pytest; one row per test and the totals.

    Part 2 (backend tests) runs against repo via MEETINGBRIEF_REPO.
    """
    import subprocess
    import xml.etree.ElementTree as ET

    xml_path = Path(out_dir) / "unit_tests.xml"
    env = {**os.environ, "MEETINGBRIEF_REPO": str(repo),
           "PYTHONIOENCODING": "utf-8"}
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTING / "app_test.py"), "-q",
         "-p", "no:cacheprovider", f"--junitxml={xml_path}"],
        cwd=str(TESTING.parent), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    seconds = round(time.perf_counter() - started, 1)
    totals = {k: 0 for k in UNIT_OUTCOMES}
    rows = []
    if not xml_path.exists():
        totals["error"] = 1
        rows.append({"test": "(pytest did not run)", "outcome": "error",
                     "seconds": seconds,
                     "message": _last_line(proc.stderr or proc.stdout)})
        return {"totals": totals, "seconds": seconds, "rows": rows}
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        outcome, message = "passed", ""
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = "failed" if child.tag == "failure" else "error"
                message = child.get("message") or ""
            elif child.tag == "skipped":
                outcome = ("xfailed" if child.get("type") == "pytest.xfail"
                           else "skipped")
                message = child.get("message") or ""
        totals[outcome] += 1
        rows.append({"test": case.get("name"), "outcome": outcome,
                     "seconds": round(float(case.get("time") or 0), 3),
                     "message": _short(message, 300)})
    return {"totals": totals, "seconds": seconds, "rows": rows}


def unit_summary(unit):
    if not unit:
        return ""
    t = unit["totals"]
    text = (f"Unit tests: {t['passed']} passed, {t['failed']} failed, "
            f"{t['error']} errors, {t['xfailed']} known gaps (xfail), "
            f"{t['skipped']} skipped.")
    broken = [r["test"] for r in unit["rows"]
              if r["outcome"] in ("failed", "error")]
    if broken:
        text += " Failing: " + ", ".join(broken[:5]) + (
            ", …" if len(broken) > 5 else "") + "."
    return text


# ---------------------------------------------------------------------------
# Stability: how much each score varies between runs of the same case
# ---------------------------------------------------------------------------

def stability_rows(report):
    """[case, score, runs, mean, min, max, spread, std] for numeric scores."""
    rows = []
    for case_id in dict.fromkeys(e["id"] for e in report):
        entries = [e for e in report if e["id"] == case_id]
        verdicts = [describe_entry(e)["verdict"] for e in entries]
        rows.append([case_id, "verdict", len(entries),
                     " / ".join(verdicts), None, None, None, None])
        for key in SCORE_KEYS:
            values = [float(v) for e in entries
                      for v in [(e.get("scores") or {}).get(key)]
                      if isinstance(v, (int, float))]
            if not values:
                continue
            mean = sum(values) / len(values)
            std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
            rows.append([case_id, key, len(values), round(mean, 2),
                         min(values), max(values),
                         round(max(values) - min(values), 2), round(std, 2)])
    return rows


def unstable_scores(report):
    """Scores whose spread between runs is large for their scale."""
    scale = {key: s for key, _, s, _ in CRITERIA}
    out = []
    for case_id, key, n, _, lo, hi, spread, _ in stability_rows(report):
        if key == "verdict" or n < 2 or spread is None:
            continue
        limit = {"0-1": 0.3, "1-5": 1.5}.get(scale.get(key))
        if limit is not None and spread >= limit:
            out.append(f"{case_id} {key} {lo}..{hi}")
    return out


# ---------------------------------------------------------------------------
# Results in plain words: the verdict and comments columns of report.xlsx
# ---------------------------------------------------------------------------

LOW_RELEVANCE = 3   # an event rated below this is not relevant to the goal
LOW_SCORE = 3       # goal_score or overall_score below this is a problem
KEY_FACT = 0.7      # expected facts at least this important must be covered
MAX_PROBLEM_LINES = 15

EVENT_PROBLEMS = (
    ("supported", "event not supported by its sources",
     "supported by their sources"),
    ("date_ok", "wrong event date", "correctly dated"),
    ("about_entity", "event about a different entity",
     "about the requested entity"),
    ("in_window", "event outside the 30-day window",
     "inside the 30-day window"),
)


def _short(text, n=140):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def describe_entry(entry):
    """Plain-words result of one run.

    Returns {"verdict": OK | ISSUES | FAIL, "problems": [(label, detail)],
    "good": [...], "text": ...}. FAIL: a check failed or something raised.
    ISSUES: checks passed, but the judge or the source check found problems.
    """
    problems, good, notes = [], [], []

    def bad(label, detail=""):
        problems.append((label, _short(detail)))

    if entry.get("error"):
        bad("backend error", _last_line(entry["error"]))
    for f in entry.get("failures") or []:
        # Leaks are listed below with the fact itself.
        if f != "backend raised an exception" and not f.startswith(
                "mentions a fact from after the meeting"):
            bad("check failed", f)
    if entry.get("judge_error"):
        bad("judge error", _last_line(entry["judge_error"]))

    judged = entry.get("judge") or {}
    scores = entry.get("scores") or {}
    events = judged.get("events") or []
    for field, label, praise in EVENT_PROBLEMS:
        verdicts = [e.get(field) for e in events]
        for e, v in zip(events, verdicts):
            if v is False:
                bad(label, f"{e.get('title')!r}: {e.get('reason', '')}")
        if events and all(v is True for v in verdicts):
            good.append(f"all {len(events)} events {praise}")
    unknown = sum(e.get("in_window") == "unknown" for e in events)
    if unknown:
        notes.append(f"{unknown} event(s): sources do not say when they "
                     "happened")
    rated = [e for e in events
             if isinstance(e.get("goal_relevance"), (int, float))]
    for e in rated:
        if e["goal_relevance"] < LOW_RELEVANCE:
            bad("event not relevant to the goal",
                f"{e.get('title')!r} ({e['goal_relevance']}/5)")
    if rated and "event_relevance" in scores:
        good.append(f"event relevance to the goal "
                    f"{scores['event_relevance']}/5 on average")
    if judged and not events:
        notes.append("no events in the briefing")

    summary = judged.get("summary")
    if summary:
        claims = summary.get("unsupported_claims") or []
        for claim in claims:
            bad("summary claim not supported by sources", claim)
        if not claims and summary.get("supported"):
            good.append("summary is supported by the sources")

    facts = judged.get("facts") or []
    for f in facts:
        if f.get("kind") == "future":
            if f.get("verdict") in VERDICT_CREDIT:
                bad("mentions a fact from after the meeting",
                    f"{f['fact']} ({f.get('date')})")
        elif f.get("verdict") == "contradicted":
            bad("contradicts an expected fact", f["fact"])
        elif f.get("verdict") not in VERDICT_CREDIT \
                and f["importance"] >= KEY_FACT:
            bad("missed an important expected fact", f["fact"])
    if any(f.get("kind") == "future" for f in facts) \
            and not scores.get("future_leaks"):
        good.append("no facts from after the meeting")
    if facts and "recall_all" in scores:
        good.append(f"covers {scores['recall_all']:.0%} of expected facts "
                    "(weighted by importance)")

    for g in judged.get("expected_gaps") or []:
        if g.get("verdict") not in VERDICT_CREDIT:
            bad("expected limitation not mentioned", g["gap"])
    for q in judged.get("gap_quality") or []:
        if q.get("specific") is False:
            bad("vague limitation", q.get("gap"))
        if q.get("is_limitation") is False:
            bad("limitation states a fact or a guess", q.get("gap"))
    if scores.get("duplicate_events"):
        bad("duplicate events",
            f"{scores['duplicate_events']} event(s) repeat another one")

    for key, label, what in (("goal", "questions", "questions and "
                              "discussion points"),
                             ("review", "overall", "overall")):
        verdict = judged.get(key) or {}
        score = verdict.get("score" if key == "goal" else "overall_score")
        if not isinstance(score, (int, float)):
            continue
        if score < LOW_SCORE:
            bad(f"low {label} score", f"{score}/5: {verdict.get('reason', '')}")
        else:
            good.append(f"{what} {score}/5")

    sources = entry.get("sources") or []
    for s in sources:
        if s["excerpt_found"] is False:
            bad("excerpt not found on the source page",
                f"{s['source_id']} {s['url']} (match {s['excerpt_match']})")
    found = [s for s in sources if s["excerpt_found"] is True]
    if found and len(found) == sum(
            isinstance(s["excerpt_found"], bool) for s in sources):
        good.append(f"all {len(found)} checked excerpts found on the "
                    "source pages")
    unreachable = scores.get("sources_unreachable")
    if unreachable:
        notes.append(f"{unreachable} source page(s) could not be read; the "
                     "judge used the backend's text for them")

    for w in entry.get("warnings") or []:
        if w.startswith("model mismatch"):
            bad("model mismatch", w.partition(": ")[2])
    if not judged and not entry.get("error"):
        notes.append("not judged (run with --judge for content scores)")
    others = [w for w in entry.get("warnings") or []
              if not w.startswith(("model mismatch", "excerpt not found"))]
    if others:
        notes.append(f"{len(others)} warning(s), see the Checks sheet")

    if entry.get("failures") or entry.get("error") or entry.get("judge_error"):
        verdict = "FAIL"
    else:
        verdict = "ISSUES" if problems else "OK"

    lines = [verdict]
    if problems:
        lines.append("What is broken:")
        lines += [f"- {label}: {detail}" if detail else f"- {label}"
                  for label, detail in problems[:MAX_PROBLEM_LINES]]
        if len(problems) > MAX_PROBLEM_LINES:
            lines.append(f"- … and {len(problems) - MAX_PROBLEM_LINES} more")
    if good:
        lines.append("What is fine:")
        lines += [f"- {g}" for g in good]
    if notes:
        lines.append("Notes:")
        lines += [f"- {n}" for n in notes]
    return {"verdict": verdict, "problems": problems, "good": good,
            "text": "\n".join(lines)}


def run_overview(report):
    """One paragraph for the whole run: verdicts and the commonest problems."""
    if not report:
        return "No runs."
    described = [describe_entry(e) for e in report]
    counts = {v: sum(d["verdict"] == v for d in described)
              for v in ("OK", "ISSUES", "FAIL")}
    text = (f"{counts['OK']} OK, {counts['ISSUES']} with issues, "
            f"{counts['FAIL']} failed, out of {len(report)} runs.")
    by_label = {}
    for e, d in zip(report, described):
        for label in {label for label, _ in d["problems"]}:
            by_label.setdefault(label, []).append(f"{e['id']}#{e['run']}")
    if by_label:
        common = sorted(by_label.items(), key=lambda kv: -len(kv[1]))[:5]
        text += " Most common problems: " + "; ".join(
            f"{label} ({len(runs)}: {', '.join(runs[:4])}"
            + (", …" if len(runs) > 4 else "") + ")"
            for label, runs in common) + "."
    return text


def run_overview_text(report, unit=None):
    """Overview of the run: unit tests, cases and unstable scores."""
    parts = [unit_summary(unit), "Cases: " + run_overview(report)]
    unstable = unstable_scores(report)
    if unstable:
        parts.append("Unstable between runs: " + "; ".join(unstable[:8])
                     + ("; …" if len(unstable) > 8 else "") + ".")
    return " ".join(p for p in parts if p)


def model_warnings(entry, agent_model, judge_model):
    """Calls that ran on a model other than the one configured."""
    out = []
    usage = entry.get("usage") or {}
    for calls, expected, who in (
            ([c for c in usage.get("backend", [])
              if c.get("stage") == "generation"], agent_model, "agent"),
            (usage.get("judge", []), judge_model, "judge")):
        used = {c.get("model") for c in calls if c.get("model")}
        if expected:
            for model in sorted(used - {expected}):
                out.append(f"model mismatch: {who} ran on {model}, "
                           f"expected {expected}")
    return out


# ---------------------------------------------------------------------------
# Excel reports
# ---------------------------------------------------------------------------

def _cell(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _add_sheet(wb, title, header, rows):
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    ws = wb.create_sheet(title)
    ws.append(header)
    for row in rows:
        ws.append([_cell(v) for v in row])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    for i, column in enumerate(ws.columns, 1):
        width = max(len(str(c.value)) for c in column if c.value is not None)
        ws.column_dimensions[get_column_letter(i)].width = min(
            max(width + 2, 8), 60)
        if width > 60:
            for c in column[1:]:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    return ws


def _last_line(text):
    return text.strip().splitlines()[-1] if text else ""


def write_xlsx(path, run_info, report, unit=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    wb.remove(wb.active)

    rows = []
    for e in report:
        scores = e.get("scores") or {}
        usage = entry_usage(e)
        described = describe_entry(e)
        rows.append([
            e["id"], e["run"], "FAIL" if e["failures"] else "PASS",
            described["verdict"], described["text"],
            len(e["failures"]), len(e["warnings"]),
            "yes" if e.get("judge_error") else "",
            *[scores.get(k) for k in SCORE_KEYS],
            *[usage[k] for k in USAGE_COLUMNS],
        ])
    means = mean_scores(report)
    rows.append([
        "TOTAL / MEAN", "", f"{run_info['passed']}/{len(report)} passed",
        "", run_info["overview"],
        sum(len(e["failures"]) for e in report),
        sum(len(e["warnings"]) for e in report),
        run_info["judge_errors"] or "",
        *[means[k] for k in SCORE_KEYS],
        *[run_info[k] for k in USAGE_COLUMNS],
    ])
    summary = _add_sheet(
        wb, "Summary",
        ["id", "run", "status", "verdict", "comments", "failures",
         "warnings", "judge_error", *SCORE_KEYS, *USAGE_COLUMNS],
        rows)
    for cell in summary[summary.max_row]:
        cell.font = Font(bold=True)
    fills = {"OK": "C6EFCE", "ISSUES": "FFEB9C", "FAIL": "FFC7CE"}
    for row in summary.iter_rows(min_row=2, min_col=4, max_col=4):
        color = fills.get(row[0].value)
        if color:
            row[0].fill = PatternFill("solid", fgColor=color)
    summary.column_dimensions["E"].width = 90
    for row in summary.iter_rows(min_row=2, min_col=5, max_col=5):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    _add_sheet(wb, "Criteria", ["key", "measures", "scale", "better"],
               [list(c) for c in CRITERIA])

    facts, events, gaps, review, checks, usage_rows = [], [], [], [], [], []
    sources = []
    for e in report:
        key = [e["id"], e["run"]]
        judged = e.get("judge") or {}
        for f in judged.get("facts", []):
            facts.append([*key, f["fact"], f.get("date"), f["kind"],
                          f.get("importance_label") or f["importance"],
                          f.get("verdict"), f.get("reason")])
        for n, ev in enumerate(judged.get("events", []), 1):
            events.append([*key, n, ev.get("title"), ev.get("date"),
                           ev.get("supported"), ev.get("date_ok"),
                           ev.get("about_entity"), ev.get("in_window"),
                           ev.get("goal_relevance"), ev.get("evidence"),
                           ev.get("reason")])
        for s in e.get("sources") or []:
            sources.append([*key, s["source_id"], s["url"], s["status"],
                            s["excerpt_match"], s["excerpt_found"],
                            s["error"]])
        for g in judged.get("expected_gaps", []):
            gaps.append([*key, "expected", g["gap"], g.get("verdict"),
                         None, None, g.get("reason")])
        for q in judged.get("gap_quality", []):
            gaps.append([*key, "briefing", q.get("gap"), None,
                         q.get("specific"), q.get("is_limitation"),
                         q.get("reason")])
        if judged:
            summary_v = judged.get("summary") or {}
            goal = judged.get("goal") or {}
            rev = judged.get("review") or {}
            review.append([
                *key, summary_v.get("supported"),
                "\n".join(summary_v.get("unsupported_claims") or []),
                goal.get("score"), goal.get("reason"),
                rev.get("overall_score"), rev.get("duplicates"),
                rev.get("reason")])
        checks += [[*key, "failure", m] for m in e["failures"]]
        checks += [[*key, "warning", m] for m in e["warnings"]]
        if e.get("error"):
            checks.append([*key, "backend error", _last_line(e["error"])])
        if e.get("judge_error"):
            checks.append([*key, "judge error", _last_line(e["judge_error"])])
        usage = e.get("usage") or {}
        for call in (usage.get("backend", []) + usage.get("fetch", [])
                     + usage.get("judge", [])):
            usage_rows.append([*key, call.get("stage"), call.get("model"),
                               *[call.get(f) for f in TOKEN_FIELDS],
                               call.get("seconds"), estimate_cost(call)])

    _add_sheet(wb, "Facts", ["id", "run", "fact", "date", "kind",
                             "importance", "verdict", "reason"], facts)
    _add_sheet(wb, "Events", ["id", "run", "n", "title", "date", "supported",
                              "date_ok", "about_entity", "in_window",
                              "goal_relevance", "evidence", "reason"], events)
    _add_sheet(wb, "Sources", ["id", "run", "source_id", "url", "status",
                               "excerpt_match", "excerpt_found", "error"],
               sources)
    _add_sheet(wb, "Gaps", ["id", "run", "kind", "text", "verdict",
                            "specific", "is_limitation", "reason"], gaps)
    _add_sheet(wb, "Review", ["id", "run", "summary_supported",
                              "unsupported_claims", "goal_score",
                              "goal_reason", "overall_score", "duplicates",
                              "review_reason"], review)
    _add_sheet(wb, "Checks", ["id", "run", "level", "message"], checks)
    _add_sheet(wb, "Usage", ["id", "run", "stage", "model", *TOKEN_FIELDS,
                             "seconds", "cost_usd"], usage_rows)
    if unit:
        ws = _add_sheet(wb, "Unit tests", ["test", "outcome", "seconds",
                                           "message"],
                        [[r["test"], r["outcome"], r["seconds"], r["message"]]
                         for r in unit["rows"]])
        colors = {"passed": "C6EFCE", "xfailed": "FFEB9C",
                  "skipped": "EDEDED", "failed": "FFC7CE", "error": "FFC7CE"}
        for row in ws.iter_rows(min_row=2, min_col=2, max_col=2):
            color = colors.get(row[0].value)
            if color:
                row[0].fill = PatternFill("solid", fgColor=color)
    _add_sheet(wb, "Stability", ["id", "score", "runs", "mean", "min", "max",
                                 "spread", "std"], stability_rows(report))
    _add_sheet(wb, "Run", ["key", "value"], [
        *[[k, v] for k, v in run_info.items()],
        ["prices_usd_per_1M (input, output, cache read)", PRICES],
    ])
    wb.save(path)


def pending_history_file(path):
    return Path(path).with_name("history_pending.json")


def append_history(path, row):
    """Append one row per run; new columns are added to the header.

    Rows that could not be written earlier (file open in Excel) are kept in
    history_pending.json and written first.
    """
    pending_file = pending_history_file(path)
    pending = (json.loads(pending_file.read_text(encoding="utf-8"))
               if pending_file.exists() else [])
    try:
        _write_history(path, pending + [row])
    except PermissionError:
        pending_file.write_text(json.dumps(pending + [row], ensure_ascii=False,
                                           indent=2, default=str),
                                encoding="utf-8")
        raise
    if pending_file.exists():
        pending_file.unlink()


def _write_history(path, rows):
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font

    if path.exists():
        wb = load_workbook(path)
        ws = wb.active
        header = [c.value for c in ws[1]]
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "History"
        header = []
    for row in rows:
        for key in row:
            if key not in header:
                header.append(key)
                ws.cell(row=1, column=len(header),
                        value=key).font = Font(bold=True)
        ws.append([_cell(row.get(key)) for key in header])
    ws.freeze_panes = "A2"
    wb.save(path)


def save_reports(xlsx_path, run_info, report, unit=None):
    history_row = {**run_info, **mean_scores(report)}
    for label, write in (
            (xlsx_path, lambda: write_xlsx(xlsx_path, run_info, report, unit)),
            (HISTORY_FILE, lambda: append_history(HISTORY_FILE, history_row))):
        try:
            write()
        except ImportError as exc:
            print(f"Excel reports skipped ({exc}): "
                  "pip install -r testing/requirements.txt")
            return
        except PermissionError:
            if label == HISTORY_FILE:
                print(f"{label} is open in Excel: this run's row is saved in "
                      f"{pending_history_file(label).name} and will be added "
                      "on the next run.")
            else:
                print(f"Could not write {label}: close it in Excel and "
                      "re-run with --from-cache.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_backend(spec):
    module_name, _, func = spec.partition(":")
    module = importlib.import_module(module_name)
    return module, getattr(module, func or "generate_briefing")


def check_file(args):
    """Check one saved model output (raw result or a saved run file)."""
    data = json.loads(args.check_file.read_text(encoding="utf-8"))
    result = data["result"] if "result" in data and "summary" not in data \
        else data

    if args.case:
        matches = [c for c in load_dataset(args.dataset)
                   if c["id"] == args.case]
        if not matches:
            raise SystemExit(f"No case {args.case!r} in {args.dataset}")
        case = matches[0]
    elif args.meeting:
        try:
            if datetime.fromisoformat(args.meeting).tzinfo is None:
                raise ValueError
        except ValueError:
            raise SystemExit("--meeting needs ISO 8601 with a UTC offset, "
                             "e.g. 2026-11-06T14:00:00+01:00")
        case = {"id": args.check_file.name, "expected_facts": [],
                "request": {"meeting_datetime": args.meeting,
                            "goal": args.goal}}
    else:
        raise SystemExit("--check-file needs --case ID or --meeting DATETIME")

    failures, warnings = deterministic_checks(case, result)
    contract_ok = not any(f.startswith("validate_result") for f in failures)
    pages = None
    if args.fetch_sources and contract_ok:
        records, pages = verify_sources(result, RESULTS / "pages")
        warnings += source_warnings(records)
        print("Sources:", source_metrics(records))
    print(f"[{'FAIL' if failures else 'PASS'}] {args.check_file.name} "
          f"(window {' .. '.join(map(str, research_window(case['request'])))})")
    for msg in failures:
        print(f"    x {msg}")
    for msg in warnings:
        print(f"    ! {msg}")

    if args.judge:
        if not contract_ok:
            print("\nJudge skipped: the output does not match the contract.")
        else:
            judge = make_judge()
            details = judge_case(judge, case, result, pages)
            for msg in leak_failures(details):
                failures.append(msg)
                print(f"    x {msg}")
            print("\nScores:", summarize_scores(details))
            print(json.dumps(details, ensure_ascii=False, indent=2))
            total = usage_totals(judge.calls)
            print(f"\nJudge: {total['calls']} calls, {total['seconds']}s, "
                  f"{total['input_tokens']:,} in / "
                  f"{total['output_tokens']:,} out tokens, "
                  f"~${total['cost_usd']:.2f}")
    sys.exit(1 if failures else 0)


def main():
    # Windows consoles and pipes default to a legacy code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if load_dotenv is not None:
        load_dotenv(TESTING.parent / ".env")
    os.environ.setdefault("ANTHROPIC_MODEL", AGENT_MODEL)

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default=TESTING / "dataset.json")
    parser.add_argument("--backend", default="demo_data:generate_briefing",
                        help="module:function, importable from the repo root")
    parser.add_argument("--runs", type=int, default=1,
                        help="runs per case, to measure variance")
    parser.add_argument("--only", nargs="*", help="case ids to run")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--skip-unit-tests", action="store_true",
                        help="do not run app_test.py before the report")
    parser.add_argument("--fetch-sources", action="store_true",
                        help="fetch source URLs, check excerpts against the "
                             "pages and judge events against them")
    parser.add_argument("--from-cache", type=Path,
                        help="re-score outputs saved by an earlier run")
    parser.add_argument("--repo", type=Path,
                        help="repository to import the backend from "
                             "(default: this checkout)")
    parser.add_argument("--xlsx", type=Path,
                        help="Excel report path (default: report.xlsx in "
                             "the run directory)")
    parser.add_argument("--check-file", type=Path,
                        help="check a saved model output instead of calling "
                             "the backend; use with --case or --meeting")
    parser.add_argument("--case", help="dataset case id for --check-file")
    parser.add_argument("--meeting",
                        help="meeting datetime with UTC offset for "
                             "--check-file without --case")
    parser.add_argument("--goal", default="",
                        help="meeting goal for --check-file without --case")
    args = parser.parse_args()
    if args.repo:
        if not args.repo.is_dir():
            raise SystemExit(f"Not a directory: {args.repo}")
        use_repo(args.repo)

    if args.check_file:
        check_file(args)

    cases = load_dataset(args.dataset)
    if args.only:
        cases = [c for c in cases
                 if c["id"] in args.only or c.get("base_id") in args.only]
    judge = make_judge() if args.judge else None
    started_at = datetime.now().astimezone()
    wall_started = time.perf_counter()

    if args.from_cache:
        out_dir = args.from_cache
        backend = probe = None
    else:
        out_dir = RESULTS / datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True)
        module, backend = load_backend(args.backend)
        probe = BackendProbe(module)

    report, any_failed = [], False
    for case in cases:
        for run in range(1, args.runs + 1):
            cache_file = out_dir / f"{case['id']}_run{run}.json"
            entry = {"id": case["id"], "run": run,
                     "usage": {"backend": [], "judge": []}}
            if backend is None:
                if not cache_file.exists():
                    continue
                saved = json.loads(cache_file.read_text(encoding="utf-8"))
                result, entry["seconds"] = saved["result"], saved.get("seconds")
                entry["error"] = saved.get("error")
                entry["usage"]["backend"] = saved.get("usage") or []
            else:
                probe.collect()  # drop anything left from an earlier call
                started = time.perf_counter()
                try:
                    result, entry["error"] = backend(dict(case["request"])), None
                except Exception:  # noqa: BLE001 - backend must raise on failure
                    result, entry["error"] = None, traceback.format_exc(limit=3)
                entry["seconds"] = round(time.perf_counter() - started, 1)
                entry["usage"]["backend"] = probe.collect()
                cache_file.write_text(json.dumps(
                    {"request": case["request"], "result": result,
                     "error": entry["error"], "seconds": entry["seconds"],
                     "usage": entry["usage"]["backend"]},
                    ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")

            if entry["error"]:
                entry["failures"] = ["backend raised an exception"]
                entry["warnings"] = []
            else:
                entry["failures"], entry["warnings"] = deterministic_checks(
                    case, result)
                contract_ok = not any(
                    f.startswith("validate_result") for f in entry["failures"])
                pages = None
                if args.fetch_sources and contract_ok:
                    records, pages = verify_sources(result, out_dir / "pages")
                    entry["sources"] = records
                    entry["warnings"] += source_warnings(records)
                    entry["usage"]["fetch"] = [
                        {"stage": "fetch", "seconds": r["seconds"]}
                        for r in records
                        if r["status"] != "no_url" and not r["cached"]]
                if judge and contract_ok:
                    mark = len(judge.calls)
                    try:
                        entry["judge"] = judge_case(judge, case, result, pages)
                        entry["scores"] = summarize_scores(entry["judge"])
                        entry["failures"] += leak_failures(entry["judge"])
                    except Exception:  # noqa: BLE001 - keep the other cases
                        entry["judge_error"] = traceback.format_exc(limit=3)
                    entry["usage"]["judge"] = judge.calls[mark:]
                if "sources" in entry:
                    entry.setdefault("scores", {}).update(
                        source_metrics(entry["sources"]))
            agent_model = (os.environ["ANTHROPIC_MODEL"]
                           if "anthropic_client" in sys.modules else None)
            entry["warnings"] += model_warnings(
                entry, agent_model, judge.model if judge else None)
            described = describe_entry(entry)
            entry["verdict"], entry["comments"] = (described["verdict"],
                                                   described["text"])
            any_failed |= bool(entry["failures"]) or bool(entry.get("judge_error"))
            report.append(entry)

            status = "FAIL" if entry["failures"] else "PASS"
            judge_s = usage_totals(entry["usage"]["judge"])["seconds"]
            print(f"[{status}] {case['id']} run {run} ({entry['seconds']}s"
                  + (f", judge {judge_s}s" if entry["usage"]["judge"] else "")
                  + ")"
                  + (f"  {entry['scores']}" if entry.get("scores") else ""))
            for msg in entry["failures"]:
                print(f"    x {msg}")
            for msg in entry["warnings"]:
                print(f"    ! {msg}")
            if entry["error"]:
                print("    " + _last_line(entry["error"]))
            if entry.get("judge_error"):
                print("    judge error: " + _last_line(entry["judge_error"]))

    report_file = out_dir / "report.json"
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    unit = None
    if not args.skip_unit_tests:
        print("\nRunning unit tests...")
        unit = run_unit_tests(out_dir, ROOT)
        print(unit_summary(unit))
    run_info = build_run_info(args, judge, report, started_at,
                              time.perf_counter() - wall_started, out_dir,
                              unit)
    xlsx_path = args.xlsx or out_dir / "report.xlsx"
    save_reports(xlsx_path, run_info, report, unit)
    print_totals(run_info)
    print(f"Models: agent {run_info['generation_models'] or '-'} "
          f"(expected {run_info['agent_model_expected']}) · judge "
          f"{run_info['judge_model'] or '-'}")
    print("\n" + run_info["overview"])
    passed = sum(not e["failures"] for e in report)
    print(f"\n{passed}/{len(report)} passed. Report: {report_file}, "
          f"{xlsx_path}. History: {HISTORY_FILE}")
    unit_failed = unit and (unit["totals"]["failed"] or unit["totals"]["error"])
    sys.exit(1 if any_failed or unit_failed else 0)


if __name__ == "__main__":
    main()
