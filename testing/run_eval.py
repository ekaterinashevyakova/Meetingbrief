"""Evaluation runner for the MeetingBrief research agent.

Runs every case in dataset.json through generate_briefing(), applies
deterministic contract checks, and optionally scores content with an
LLM judge through an OpenAI-compatible endpoint (e.g. the Bifrost gateway).

Usage (from the repository root):

    python testing/run_eval.py                          # demo backend, checks only
    python testing/run_eval.py --backend backend:generate_briefing --runs 3
    python testing/run_eval.py --from-cache testing/results/<run>  # re-score saved outputs
    python testing/run_eval.py --judge                  # also run the LLM judge
    python testing/run_eval.py --repo ../mb-bifrost --backend briefing_generator

Judge configuration (environment variables, never hard-coded):
    JUDGE_BASE_URL  e.g. http://localhost:8080/v1
    JUDGE_API_KEY   optional, sent as a Bearer token
    JUDGE_AUTH_HEADER  optional full Authorization value (e.g. BIFROST_BASIC_AUTH)
    JUDGE_MODEL     model name as the gateway expects it
"""

import argparse
import ast
import importlib
import json
import os
import sys
import time
import traceback
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

TESTING = Path(__file__).resolve().parent
# Repository whose app.py and backend are tested. Defaults to this checkout;
# --repo points it at another one (e.g. a git worktree of a teammate's branch).
ROOT = TESTING.parent

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

def load_dataset(path):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    seen = set()
    for i, case in enumerate(cases):
        cid = case.get("id")
        label = cid or f"#{i}"
        if not cid:
            problems.append(f"{label}: missing id")
        elif cid in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(cid)
        req = case.get("request", {})
        for field in REQUEST_FIELDS:
            if not isinstance(req.get(field), str):
                problems.append(f"{label}: request.{field} must be a string")
        if req.get("entity_type") not in ("company", "person"):
            problems.append(f"{label}: entity_type must be company or person")
        try:
            if datetime.fromisoformat(req["meeting_datetime"]).tzinfo is None:
                problems.append(f"{label}: meeting_datetime needs a UTC offset")
        except (KeyError, TypeError, ValueError):
            problems.append(f"{label}: invalid meeting_datetime")
        for fact in case.get("expected_facts", []):
            if not 0 < fact.get("importance", 0) <= 1:
                problems.append(f"{label}: importance must be in (0, 1]")
            if fact.get("kind") not in ("recent", "background"):
                problems.append(f"{label}: fact kind must be recent/background")
        if "gaps" in case:
            problems.append(f"{label}: use 'expected_gaps', not 'gaps'")
        gaps = case.get("expected_gaps", [])
        if not isinstance(gaps, list) or not all(
                isinstance(g, str) and g.strip() for g in gaps):
            problems.append(f"{label}: expected_gaps must be non-empty strings")
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
# LLM judge (optional)
# ---------------------------------------------------------------------------

class Judge:
    def __init__(self):
        self.base = os.environ.get("JUDGE_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("JUDGE_MODEL", "")
        self.key = os.environ.get("JUDGE_API_KEY", "")
        # Full Authorization header value, e.g. the same one as
        # BIFROST_BASIC_AUTH ("Basic ..."). Takes precedence over the key.
        self.auth = os.environ.get("JUDGE_AUTH_HEADER", "")
        if not self.base or not self.model:
            raise SystemExit("--judge needs JUDGE_BASE_URL and JUDGE_MODEL")

    def ask(self, prompt):
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content":
                    "You are a strict evaluator. Answer with a single JSON "
                    "object and nothing else."},
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
            content = json.load(resp)["choices"][0]["message"]["content"]
        start, end = content.find("{"), content.rfind("}")
        return json.loads(content[start:end + 1])

    def fact_coverage(self, briefing_text, fact):
        return self.ask(
            "Briefing:\n<<<\n" + briefing_text + "\n>>>\n\n"
            f"Expected fact: {fact}\n\n"
            "Does the briefing state this fact (same meaning, wording may "
            "differ)? Reply {\"verdict\": \"covered\" | \"not_covered\" | "
            "\"contradicted\", \"reason\": \"...\"}."
        )

    def event_support(self, event, excerpts):
        return self.ask(
            f"Claim: {event['title']}. {event['description']}\n\n"
            "Cited source excerpts:\n<<<\n" + "\n---\n".join(excerpts)
            + "\n>>>\n\nIs every factual statement in the claim supported by "
            "the excerpts? Reply {\"supported\": true | false, "
            "\"reason\": \"...\"}."
        )

    def summary_support(self, summary, evidence):
        return self.ask(
            f"Summary: {summary}\n\nSources:\n<<<\n"
            + "\n---\n".join(evidence)
            + "\n>>>\n\nList every statement in the summary that the sources "
            "do not support. Reply {\"supported\": true | false, "
            "\"unsupported_claims\": [\"...\"]}."
        )

    def gap_coverage(self, gaps, expected_gap):
        return self.ask(
            "Limitations listed in a briefing:\n- " + "\n- ".join(gaps or ["(none)"])
            + f"\n\nExpected limitation: {expected_gap}\n\n"
            "Is this limitation mentioned (same meaning, wording may differ)? "
            "Reply {\"verdict\": \"covered\" | \"not_covered\", "
            "\"reason\": \"...\"}."
        )

    def gap_quality(self, goal, gaps):
        return self.ask(
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
        return self.ask(
            f"Meeting goal: {goal or '(not specified)'}\n\n"
            "Suggested questions and discussion points:\n- "
            + "\n- ".join(i for i in items if i)
            + "\n\nRate from 1 to 5 how useful and specific these are for "
            "this goal. Reply {\"score\": 1-5, \"reason\": \"...\"}."
        )


def judge_case(judge, case, result):
    scores = {"facts": [], "events": [], "goal": None}
    text = all_text(result)
    for fact in case.get("expected_facts", []):
        v = judge.fact_coverage(text, fact["fact"])
        scores["facts"].append({**fact, **v})
    # Prefer full article text (optional backend field) over the short excerpt.
    evidence = {
        s["id"]: s.get("article_text") or s.get("excerpt") or ""
        for s in result["sources"]
    }
    for e in result["events"]:
        v = judge.event_support(e, [evidence[r] for r in e["source_ids"]])
        scores["events"].append({"title": e["title"], **v})
    scores["summary"] = judge.summary_support(
        result["summary"], list(evidence.values()))
    scores["goal"] = judge.goal_relevance(case["request"]["goal"], result)
    scores["expected_gaps"] = [
        {"gap": g, **judge.gap_coverage(result["gaps"], g)}
        for g in case.get("expected_gaps", [])
    ]
    if result["gaps"]:
        scores["gap_quality"] = judge.gap_quality(
            case["request"]["goal"], result["gaps"]).get("items", [])
    return scores


def summarize_scores(scores):
    out = {}
    facts = scores["facts"]
    for kind in ("all", "recent", "background"):
        subset = [f for f in facts if kind == "all" or f["kind"] == kind]
        total = sum(f["importance"] for f in subset)
        if total:
            got = sum(f["importance"] for f in subset
                      if f.get("verdict") == "covered")
            out[f"recall_{kind}"] = round(got / total, 2)
    out["contradicted"] = sum(f.get("verdict") == "contradicted" for f in facts)
    if scores["events"]:
        ok = sum(bool(e.get("supported")) for e in scores["events"])
        out["groundedness"] = round(ok / len(scores["events"]), 2)
    if scores.get("summary"):
        out["summary_supported"] = bool(scores["summary"].get("supported"))
    if scores["goal"]:
        out["goal_score"] = scores["goal"].get("score")
    expected = scores.get("expected_gaps", [])
    if expected:
        got = sum(g.get("verdict") == "covered" for g in expected)
        out["gap_recall"] = round(got / len(expected), 2)
    quality = scores.get("gap_quality", [])
    if quality:
        out["vague_gaps"] = sum(not q.get("specific", True) for q in quality)
        out["non_limitation_gaps"] = sum(
            not q.get("is_limitation", True) for q in quality)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_backend(spec):
    module, _, func = spec.partition(":")
    return getattr(importlib.import_module(module), func or "generate_briefing")


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
    print(f"[{'FAIL' if failures else 'PASS'}] {args.check_file.name} "
          f"(window {' .. '.join(map(str, research_window(case['request'])))})")
    for msg in failures:
        print(f"    x {msg}")
    for msg in warnings:
        print(f"    ! {msg}")

    if args.judge:
        if any(f.startswith("validate_result") for f in failures):
            print("\nJudge skipped: the output does not match the contract.")
        else:
            details = judge_case(Judge(), case, result)
            print("\nScores:", summarize_scores(details))
            print(json.dumps(details, ensure_ascii=False, indent=2))
    sys.exit(1 if failures else 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", default=TESTING / "dataset.json")
    parser.add_argument("--backend", default="demo_data:generate_briefing",
                        help="module:function, importable from the repo root")
    parser.add_argument("--runs", type=int, default=1,
                        help="runs per case, to measure variance")
    parser.add_argument("--only", nargs="*", help="case ids to run")
    parser.add_argument("--judge", action="store_true")
    parser.add_argument("--from-cache", type=Path,
                        help="re-score outputs saved by an earlier run")
    parser.add_argument("--repo", type=Path,
                        help="repository to import the backend from "
                             "(default: this checkout)")
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
        cases = [c for c in cases if c["id"] in args.only]
    judge = Judge() if args.judge else None

    if args.from_cache:
        out_dir = args.from_cache
        backend = None
    else:
        out_dir = TESTING / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True)
        backend = load_backend(args.backend)

    report, any_failed = [], False
    for case in cases:
        for run in range(1, args.runs + 1):
            cache_file = out_dir / f"{case['id']}_run{run}.json"
            entry = {"id": case["id"], "run": run}
            if backend is None:
                if not cache_file.exists():
                    continue
                saved = json.loads(cache_file.read_text(encoding="utf-8"))
                result, entry["seconds"] = saved["result"], saved.get("seconds")
                entry["error"] = saved.get("error")
            else:
                started = time.perf_counter()
                try:
                    result, entry["error"] = backend(dict(case["request"])), None
                except Exception:  # noqa: BLE001 - backend must raise on failure
                    result, entry["error"] = None, traceback.format_exc(limit=3)
                entry["seconds"] = round(time.perf_counter() - started, 1)
                cache_file.write_text(json.dumps(
                    {"request": case["request"], "result": result,
                     "error": entry["error"], "seconds": entry["seconds"]},
                    ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")

            if entry["error"]:
                entry["failures"] = ["backend raised an exception"]
                entry["warnings"] = []
            else:
                entry["failures"], entry["warnings"] = deterministic_checks(
                    case, result)
                if judge and not any(
                        f.startswith("validate_result") for f in entry["failures"]):
                    entry["judge"] = judge_case(judge, case, result)
                    entry["scores"] = summarize_scores(entry["judge"])
            any_failed |= bool(entry["failures"])
            report.append(entry)

            status = "FAIL" if entry["failures"] else "PASS"
            print(f"[{status}] {case['id']} run {run} ({entry['seconds']}s)"
                  + (f"  {entry['scores']}" if entry.get("scores") else ""))
            for msg in entry["failures"]:
                print(f"    x {msg}")
            for msg in entry["warnings"]:
                print(f"    ! {msg}")
            if entry["error"]:
                print("    " + entry["error"].strip().splitlines()[-1])

    report_file = out_dir / "report.json"
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    passed = sum(not e["failures"] for e in report)
    print(f"\n{passed}/{len(report)} passed. Report: {report_file}")
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
