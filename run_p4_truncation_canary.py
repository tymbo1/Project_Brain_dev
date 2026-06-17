"""
P4 truncation canary — substrate-only audit seams #5 + #8.

In-process binary witness. No LLM, no HTTP.

Substrate occasionally carries truncated OEWN glosses or capsule snippets
("refers to determine the essential quality of", "a visual representation of
the relations between certain quantities, represented"). The realizer's
_trim_truncated filter drops sentences whose tail is grammatically dangling.

Cases:
  • Truncation cases — prompts whose substrate is known to surface fragments.
    Assert: realized text does NOT end with a dangling preposition / comma /
    conjunction before terminal punctuation.
  • Control cases — confident responses must retain substantive content; we
    don't accept the trivial "filter dropped everything" failure mode.
"""
from __future__ import annotations
import json
import re
import sys

from inference.activation_engine import ActivationEngine
from cognitive_operators.pipeline import run_pipeline as cog_run
from language_cognition.pipeline import run_language_cognition

_DANGLING_TAIL_WORDS = (
    "of", "to", "with", "for", "from", "in", "on", "by", "at", "about", "as",
    "into", "onto", "upon", "via", "through", "across", "between", "among",
    "and", "or", "but", "nor",
    "that", "which", "who", "whom", "whose", "when", "where", "than", "if",
    "is", "are", "was", "were", "be", "been", "being", "the", "a", "an",
)
_DANGLING_RE = re.compile(
    r"(?:^|[\s\"'(])(" + "|".join(_DANGLING_TAIL_WORDS) + r")[.!?:—,;\"')\s]*$",
    re.IGNORECASE,
)
_TRAILING_COMMA_RE = re.compile(r",[\s\"')]*[.!?]?\s*$")


CASES = [
    # (id, prompt, kind)
    ("trunc_entropy", "Define entropy.",                  "truncation"),
    ("trunc_graph",   "What is a graph?",                 "truncation"),
    ("trunc_soul",    "What is the purpose of the soul?", "truncation"),
    ("ctrl_lonely",   "I feel lonely lately and the pain is hard.", "control"),
    ("ctrl_plan",     "Help me build a practical plan with clear steps.", "control"),
]


def _per_sentence_check(text: str) -> dict:
    """Return per-sentence dangling/comma flags. Skips questions (English
    legitimately strands prepositions in interrogatives — "to start with?")."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    bad = []
    for p in parts:
        ps = p.strip()
        if not ps:
            continue
        if ps.rstrip().endswith("?"):
            continue
        if _TRAILING_COMMA_RE.search(ps) or _DANGLING_RE.search(ps):
            bad.append(ps[-80:])
    return {"sentence_count": len(parts), "bad_count": len(bad), "bad_tails": bad}


def main() -> int:
    engine = ActivationEngine()
    results = []
    total_pass = 0
    for cid, prompt, kind in CASES:
        try:
            res = engine.infer(prompt, max_chains=12)
            chains = res.get("chains", [])
            plan = cog_run(query=prompt, chains=chains, source_lane="knowledge")
            lc = run_language_cognition(query=prompt, response_plan=plan)

            sent_check = _per_sentence_check(lc.text)
            no_dangling = sent_check["bad_count"] == 0

            substantive_chars = len(re.sub(r"\s+", " ", (lc.text or "")).strip())
            has_content = substantive_chars >= 40

            if kind == "truncation":
                case_pass = no_dangling and has_content
            else:
                case_pass = no_dangling and has_content

            if case_pass:
                total_pass += 1
            results.append({
                "id": cid,
                "kind": kind,
                "prompt": prompt,
                "speech_act": lc.speech_act,
                "checks": {
                    "no_dangling_tail": no_dangling,
                    "has_content": has_content,
                    "bad_tails": sent_check["bad_tails"],
                },
                "text_tail": (lc.text or "")[-200:],
                "pass": case_pass,
            })
        except Exception as e:
            results.append({"id": cid, "kind": kind, "prompt": prompt, "error": str(e), "pass": False})

    overall = total_pass == len(CASES)
    print(json.dumps({
        "P4_TRUNCATION_CANARY_PASS": overall,
        "passed": total_pass,
        "total": len(CASES),
        "cases": results,
    }, indent=2))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
