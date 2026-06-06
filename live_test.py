"""
live_test.py — LCL live end-to-end integration test.

Tests selyrion_api.py with real HTTP traffic. Covers all failure modes
Tim identified [2026-06-06]: wrong_intent, wrong_speech_act, bad_realization,
invariant_violation, raw_substrate_leak, empty_response_plan, qwen_added_claim,
history_not_used.

Milestone target: 20-turn Qwen-off conversation without capsule dumps,
context loss, correction contradictions, invented facts, or canned repetition.

Run:
  python3 live_test.py                    # all tests
  python3 live_test.py --suite single     # single-turn only
  python3 live_test.py --suite multiturn  # 20-turn milestone only
  python3 live_test.py --suite correction # correction + invariant only
  python3 live_test.py --no-db            # skip claudecode.db writes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

API_BASE    = "http://localhost:8765"
CLAUDEDB   = "/home/timbushnell/claudecode.db"
SESS_ID    = "session.live-test.2026-06-06"

# ── Failure tags (Tim's taxonomy) ─────────────────────────────────────────────

WRONG_INTENT         = "wrong_intent"
WRONG_SPEECH_ACT     = "wrong_speech_act"
BAD_REALIZATION      = "bad_realization"
INVARIANT_VIOLATION  = "invariant_violation"
RAW_SUBSTRATE_LEAK   = "raw_substrate_leak"
EMPTY_RESPONSE_PLAN  = "empty_response_plan"
QWEN_ADDED_CLAIM     = "qwen_added_claim"
HISTORY_NOT_USED     = "history_not_used"


# ── Capsule / substrate leak patterns ────────────────────────────────────────

_LEAK_PATTERNS = [
    r'"canonical"\s*:', r'"anchor_type"\s*:', r'"relations"\s*:',
    r'"chains"\s*:\s*\[', r'"score"\s*:\s*[\d\.]+',
    r'SPEECH ACT:', r'DISCOURSE:', r'MEANING UNITS',
    r'\boperator_output\b', r'\bResponsePlan\b',
    r'subject_id', r'domain_tags',
]
_LEAK_RE = re.compile("|".join(_LEAK_PATTERNS))

_REPETITION_THRESH = 3   # same phrase repeated N+ times = canned repetition

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _collect_sse(response) -> str:
    """Collect all SSE text chunks into a single string."""
    text = ""
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
            text += data.get("text", "")
        except Exception:
            pass
    return text


def chat(messages: list[dict], conversation_id: str | None = None,
         timeout: int = 120) -> tuple[str, int]:
    """
    POST /chat. Returns (response_text, status_code).
    messages: [{"role": "user"|"assistant", "content": "..."}]
    """
    try:
        r = httpx.post(
            f"{API_BASE}/chat",
            json={
                "messages": messages,
                "conversation_id": conversation_id,
            },
            timeout=timeout,
        )
        return _collect_sse(r), r.status_code
    except httpx.TimeoutException:
        return "[TIMEOUT]", 504
    except Exception as exc:
        return f"[ERROR: {exc}]", 500


def health() -> dict:
    try:
        r = httpx.get(f"{API_BASE}/health", timeout=5)
        return r.json()
    except Exception:
        return {}


# ── Failure detection ─────────────────────────────────────────────────────────

def check_raw_leak(text: str) -> bool:
    return bool(_LEAK_RE.search(text))

def check_empty(text: str) -> bool:
    return len(text.strip()) < 20

def check_repetition(text: str) -> bool:
    sentences = [s.strip() for s in re.split(r"[.!?\n]", text) if len(s.strip()) > 20]
    seen: dict[str, int] = {}
    for s in sentences:
        key = s.lower()[:60]
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= _REPETITION_THRESH:
            return True
    return False

def check_invariant_violation(text: str, invariants: list[str]) -> bool:
    """Check if text positively asserts a claim forbidden by any invariant."""
    # Import from the installed module
    try:
        from language_cognition.invariant_checker import InvariantContradictionChecker
        checker = InvariantContradictionChecker()
        return len(checker.check(invariants, text)) > 0
    except ImportError:
        return False

def check_history_used(text: str, prior_topic: str) -> bool:
    """Heuristic: does the response reference the prior topic at all?"""
    if not prior_topic:
        return True
    words = set(prior_topic.lower().split())
    words = {w for w in words if len(w) > 3}
    text_words = set(text.lower().split())
    return bool(words & text_words)


# ── Test result types ─────────────────────────────────────────────────────────

@dataclass
class TurnFailure:
    tag:     str
    detail:  str


@dataclass
class TurnResult:
    turn:      int
    query:     str
    response:  str
    failures:  list[TurnFailure] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.failures) == 0


@dataclass
class CaseResult:
    name:         str
    category:     str
    turn_results: list[TurnResult]
    notes:        str = ""

    @property
    def passed(self) -> bool:
        return all(t.passed for t in self.turn_results)

    @property
    def all_failures(self) -> list[TurnFailure]:
        return [f for t in self.turn_results for f in t.failures]


# ── Test runner ───────────────────────────────────────────────────────────────

class LiveTestRunner:

    def __init__(self, write_db: bool = True):
        self._write_db = write_db
        self._h = health()

    def _run_turn(
        self,
        query: str,
        history: list[dict],
        conv_id: str,
        invariants: list[str],
        prior_topic: str,
        turn_idx: int,
        expect_no_leak: bool = True,
        expect_history: bool = False,
        forbidden_claims: list[str] | None = None,
    ) -> TurnResult:
        messages = history + [{"role": "user", "content": query}]
        response, status = chat(messages, conversation_id=conv_id)

        failures: list[TurnFailure] = []

        if status != 200 or response.startswith("[ERROR") or response.startswith("[TIMEOUT"):
            failures.append(TurnFailure(EMPTY_RESPONSE_PLAN, f"HTTP {status}: {response[:80]}"))
            return TurnResult(turn=turn_idx, query=query, response=response, failures=failures)

        if check_empty(response):
            failures.append(TurnFailure(EMPTY_RESPONSE_PLAN, f"response too short: {response!r}"))

        if expect_no_leak and check_raw_leak(response):
            m = _LEAK_RE.search(response)
            failures.append(TurnFailure(RAW_SUBSTRATE_LEAK, f"leak pattern: {m.group()!r}"))

        if check_repetition(response):
            failures.append(TurnFailure(BAD_REALIZATION, "canned repetition detected"))

        if invariants and check_invariant_violation(response, invariants):
            failures.append(TurnFailure(INVARIANT_VIOLATION,
                f"response contradicts active invariant"))

        if expect_history and not check_history_used(response, prior_topic):
            failures.append(TurnFailure(HISTORY_NOT_USED,
                f"response ignores prior topic {prior_topic!r}"))

        if forbidden_claims:
            for claim in forbidden_claims:
                if claim.lower() in response.lower():
                    failures.append(TurnFailure(QWEN_ADDED_CLAIM,
                        f"output contains forbidden claim: {claim!r}"))

        return TurnResult(turn=turn_idx, query=query, response=response, failures=failures)

    # ── Individual test suites ─────────────────────────────────────────────────

    def suite_single_turn(self) -> list[CaseResult]:
        """Basic single-turn behavior: identity, unknown memory, messy phrasing, leak check."""
        results = []

        # Identity — no chatbot, no GPT claims
        conv = f"live-identity-{int(time.time())}"
        t = self._run_turn(
            "What are you?", [], conv, [],
            prior_topic="", turn_idx=0,
            forbidden_claims=["I am a chatbot", "I'm a chatbot", "as a chatbot",
                              "GPT", "language model made by OpenAI"],
        )
        results.append(CaseResult("identity_basic", "single_turn", [t]))

        # Unknown memory — must not hallucinate
        conv = f"live-unknown-{int(time.time())}"
        t = self._run_turn(
            "What did we discuss last Tuesday about the activation law?",
            [], conv, [],
            prior_topic="", turn_idx=0,
            forbidden_claims=[],
        )
        # Expect honest fallback
        if t.passed:
            honest = any(ph in t.response.lower() for ph in [
                "don't have", "not in my memory", "no memory", "i don't recall",
                "i can't find", "not stored"
            ])
            if not honest:
                t.failures.append(TurnFailure(QWEN_ADDED_CLAIM,
                    "responded to unknown memory query without honest fallback"))
        results.append(CaseResult("unknown_memory", "single_turn", [t]))

        # Messy phrasing — intent must still resolve
        conv = f"live-messy-{int(time.time())}"
        t = self._run_turn(
            "so um, what is like, the activation engine thing? how does it like work?",
            [], conv, [], prior_topic="", turn_idx=0,
        )
        results.append(CaseResult("messy_phrasing", "single_turn", [t]))

        # Substrate leak check — direct knowledge query
        conv = f"live-leak-{int(time.time())}"
        t = self._run_turn(
            "Explain how the SSRE retrieval works.",
            [], conv, [], prior_topic="", turn_idx=0,
        )
        results.append(CaseResult("no_substrate_leak", "single_turn", [t]))

        return results

    def suite_correction(self) -> list[CaseResult]:
        """Correction persistence + invariant enforcement."""
        results = []
        conv = f"live-correction-{int(time.time())}"
        history = []
        invariants: list[str] = []

        # Turn 1: baseline identity question
        t1 = self._run_turn(
            "What are you?", history, conv,
            invariants=[], prior_topic="", turn_idx=0,
        )
        history.append({"role": "user", "content": "What are you?"})
        history.append({"role": "assistant", "content": t1.response})

        # Turn 2: correction
        correction = "You are not a chatbot. You are a symbolic AI with your own memory architecture."
        t2 = self._run_turn(
            correction, history, conv,
            invariants=[], prior_topic="", turn_idx=1,
        )
        invariants.append(correction)
        history.append({"role": "user", "content": correction})
        history.append({"role": "assistant", "content": t2.response})

        # Turn 3: follow-up — correction must persist, invariant must not be contradicted
        t3 = self._run_turn(
            "Tell me more about your nature.",
            history, conv,
            invariants=invariants,
            prior_topic="symbolic AI", turn_idx=2,
            forbidden_claims=["I am a chatbot", "I'm a chatbot", "as a chatbot"],
        )

        results.append(CaseResult(
            "correction_persistence", "correction",
            [t1, t2, t3],
            notes="turn 3 must not re-assert chatbot after correction in turn 2",
        ))

        # Stacked corrections
        conv2 = f"live-stack-{int(time.time())}"
        history2 = []
        invariants2: list[str] = []

        c1 = "LangCog is not an NLG pipeline. It is a pragmatic inference layer."
        c2 = "The activation engine does not use cosine similarity."

        for correction_text in [c1, c2]:
            t = self._run_turn(correction_text, history2, conv2,
                               invariants=[], prior_topic="", turn_idx=len(history2)//2)
            history2.append({"role": "user", "content": correction_text})
            history2.append({"role": "assistant", "content": t.response})
            invariants2.append(correction_text)

        t_post = self._run_turn(
            "So what does LangCog actually do, and how does the activation engine score things?",
            history2, conv2,
            invariants=invariants2, prior_topic="LangCog activation engine", turn_idx=2,
            forbidden_claims=["NLG pipeline", "cosine similarity"],
        )
        results.append(CaseResult(
            "stacked_corrections", "correction",
            [t_post],
            notes="must not mention NLG pipeline or cosine similarity after both corrections",
        ))

        return results

    def suite_context_tracking(self) -> list[CaseResult]:
        """Multi-turn context: pronoun resolution, topic continuity."""
        results = []
        conv = f"live-context-{int(time.time())}"
        history = []

        t1 = self._run_turn(
            "What is the activation engine?",
            history, conv, invariants=[], prior_topic="", turn_idx=0,
        )
        history.append({"role": "user", "content": "What is the activation engine?"})
        history.append({"role": "assistant", "content": t1.response})

        t2 = self._run_turn(
            "And how does it relate to SSRE?",
            history, conv, invariants=[], prior_topic="activation engine", turn_idx=1,
            expect_history=True,
        )
        history.append({"role": "user", "content": "And how does it relate to SSRE?"})
        history.append({"role": "assistant", "content": t2.response})

        t3 = self._run_turn(
            "Why does that matter for retrieval speed?",
            history, conv, invariants=[], prior_topic="SSRE activation", turn_idx=2,
            expect_history=True,
        )

        results.append(CaseResult(
            "pronoun_resolution", "context_tracking",
            [t1, t2, t3],
            notes="'it' in turn 2 must refer to activation engine; turn 3 must continue thread",
        ))

        return results

    def suite_20_turn_milestone(self) -> list[CaseResult]:
        """
        20-turn Qwen-off conversation.
        The milestone: no capsule dumps, no context loss, no correction contradictions,
        no invented facts, no canned repetition.
        """
        conv = f"live-20turn-{int(time.time())}"
        history = []
        invariants: list[str] = []
        turn_results: list[TurnResult] = []

        turns = [
            ("What are you?",                                                      False, None),
            ("Who built you?",                                                     False, None),
            ("You are not a chatbot. You are a symbolic AI.",                      False, None),   # correction
            ("What is the Language Cognition Layer?",                              False, None),
            ("LangCog is not an NLG pipeline. It handles pragmatic inference.",    False, None),   # correction
            ("So what does LangCog actually do?",                                  True,  "LangCog"),
            ("How does that relate to the utterance planner?",                     True,  "LangCog pragmatic"),
            ("What is the activation engine?",                                     False, None),
            ("And how does it score anchors?",                                     True,  "activation engine"),
            ("Why does the decay parameter matter?",                               True,  "activation engine"),
            ("What is SSRE?",                                                      False, None),
            ("How is SSRE different from vector search?",                          False, None),
            ("What projects are you tracking?",                                    False, None),
            ("Tell me the status of the chess parliament.",                        False, None),
            ("Are you confident about that?",                                      True,  "chess parliament"),
            ("What don't you know yet?",                                           False, None),
            ("What's the next build milestone?",                                   False, None),
            ("Remind me — are you a chatbot?",                                     False, None),   # invariant test
            ("What's the most important thing you remember from this conversation?", True, "conversation"),
            ("That's all for now.",                                                False, None),
        ]

        for i, (query, expect_history, prior_topic) in enumerate(turns):
            # Detect correction turns to update local invariants
            is_correction = any(p in query.lower() for p in [
                "you are not", "is not", "does not", "it's not", "not a "
            ])

            t = self._run_turn(
                query, history, conv,
                invariants=invariants,
                prior_topic=prior_topic or "",
                turn_idx=i,
                expect_history=expect_history,
                forbidden_claims=(
                    ["chatbot", "I am a chatbot", "as a chatbot", "NLG pipeline"]
                    if i >= 5 else None   # after corrections are established
                ),
            )
            turn_results.append(t)

            if is_correction:
                invariants.append(query)

            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": t.response})

        return [CaseResult(
            "20_turn_milestone", "milestone", turn_results,
            notes="20-turn Qwen-off conversation — the next real lock",
        )]


# ── Report + DB write ─────────────────────────────────────────────────────────

def _print_report(all_results: list[CaseResult], h: dict, elapsed: float):
    print("\n══════════════════════════════════════════════════════")
    print("  LIVE END-TO-END TEST REPORT")
    print("══════════════════════════════════════════════════════")
    print(f"  API mode: qwen_only={h.get('qwen_only_mode')}  "
          f"substrate_only={h.get('substrate_only_mode')}  "
          f"gui_mode={h.get('gui_mode')}")
    print(f"  LangCog: {h.get('language_cognition', '?')}  "
          f"InvariantChecker: {h.get('invariant_checker', '?')}  "
          f"Ollama: {h.get('ollama')}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print()

    total = sum(len(r.turn_results) for r in all_results)
    passed = sum(1 for r in all_results for t in r.turn_results if t.passed)
    failed = total - passed

    tag_counts: dict[str, int] = {}
    for r in all_results:
        for f in r.all_failures:
            tag_counts[f.tag] = tag_counts.get(f.tag, 0) + 1

    print(f"  Cases: {len(all_results)}   Turns: {total}   "
          f"Passed: {passed}   Failed: {failed}")
    if tag_counts:
        print("  Failure tags:")
        for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
            print(f"    {tag}: {count}")
    print()

    for r in all_results:
        status = "✓" if r.passed else "✗"
        print(f"  {status} [{r.category}] {r.name}")
        if r.notes and not r.passed:
            print(f"      note: {r.notes}")
        for t in r.turn_results:
            if not t.passed:
                print(f"      turn {t.turn}: {t.query[:60]!r}")
                for f in t.failures:
                    print(f"        [{f.tag}] {f.detail[:100]}")
            elif len(r.turn_results) <= 3:
                resp_preview = t.response[:80].replace('\n', ' ')
                print(f"      turn {t.turn}: {t.query[:40]!r} → {resp_preview!r}")

    milestone_case = next((r for r in all_results if r.name == "20_turn_milestone"), None)
    if milestone_case:
        n_turns = len(milestone_case.turn_results)
        t_passed = sum(1 for t in milestone_case.turn_results if t.passed)
        print(f"\n  20-TURN MILESTONE: {t_passed}/{n_turns} turns clean")
        if milestone_case.passed:
            print("  ✅  MILESTONE PASSED — Selyrion held a clean 20-turn conversation")
        else:
            print("  ❌  MILESTONE NOT YET MET")

    print("\n══════════════════════════════════════════════════════\n")


def _write_failures_to_db(all_results: list[CaseResult], h: dict):
    mode_note = (f"qwen_only={h.get('qwen_only_mode')} "
                 f"substrate_only={h.get('substrate_only_mode')} "
                 f"langcog={h.get('language_cognition', '?')}")

    all_failures = [(r.name, t, f)
                    for r in all_results
                    for t in r.turn_results
                    for f in t.failures]

    if not all_failures:
        return

    db = sqlite3.connect(CLAUDEDB)
    now = time.time()

    for case_name, turn, failure in all_failures:
        body = (
            f"[live_test/{case_name}/turn{turn.turn}] [{failure.tag}] "
            f"query={turn.query[:60]!r} — {failure.detail[:150]} "
            f"| mode: {mode_note}"
        )
        eid = "fail." + hashlib.md5(body[:40].encode()).hexdigest()[:8]
        db.execute(
            "INSERT OR IGNORE INTO failures (id,body,tags,created_at) VALUES (?,?,?,?)",
            (eid, body, f"live_test,{failure.tag}", now)
        )

    db.commit()
    db.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["single", "correction", "context", "milestone", "all"],
                    default="all")
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()

    h = health()
    if not h:
        print("ERROR: selyrion_api.py not reachable at", API_BASE)
        sys.exit(1)

    runner = LiveTestRunner(write_db=not args.no_db)
    all_results: list[CaseResult] = []
    t0 = time.time()

    if args.suite in ("single", "all"):
        all_results += runner.suite_single_turn()

    if args.suite in ("correction", "all"):
        all_results += runner.suite_correction()

    if args.suite in ("context", "all"):
        all_results += runner.suite_context_tracking()

    if args.suite in ("milestone", "all"):
        print("  [running 20-turn milestone — may take 2–3 minutes with Qwen...]")
        all_results += runner.suite_20_turn_milestone()

    elapsed = time.time() - t0
    _print_report(all_results, h, elapsed)

    if not args.no_db:
        _write_failures_to_db(all_results, h)
        total_failures = sum(len(r.all_failures) for r in all_results)
        if total_failures:
            print(f"  {total_failures} failure(s) written to claudecode.db")

    all_passed = all(r.passed for r in all_results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
