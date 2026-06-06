"""
invariant_checker.py — Gate 3: Invariant non-contradiction checker.

Verifies that a candidate output text does not contradict any
active DialogueMemory invariants.

Algorithm:
  For each invariant:
    1. Extract the negated claim — what the invariant forbids
    2. Check if the output makes a positive assertion of that claim
    3. If so → ContradictionResult with evidence

"Positive assertion" = the forbidden term appears in the output
WITHOUT a nearby preceding negation word.

Design principle: prefer false negatives over false positives.
An uncaught contradiction is less harmful than blocking valid output.
The checker is a safety net, not a grammar enforcer.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


# ── Forbidden claim ───────────────────────────────────────────────────────────

@dataclass
class ForbiddenClaim:
    forbidden:   str     # the concept/predicate that must not be asserted
    subject:     str     # the subject it applies to (may be empty = any subject)
    pattern:     str     # how this was extracted: "is_not" / "does_not_use" / "explicit"
    source_text: str     # the invariant text it came from


# ── Contradiction result ──────────────────────────────────────────────────────

@dataclass
class ContradictionResult:
    invariant:    str
    forbidden:    str
    evidence:     str    # the span in the output that triggered it
    position:     int    # char offset in the output


# ── Negation vocabulary ───────────────────────────────────────────────────────

_NEGATIONS: frozenset[str] = frozenset({
    "not", "no", "never", "neither", "nor",
    "isn't", "aren't", "doesn't", "don't", "won't", "can't",
    "without", "avoids", "avoid", "avoiding", "avoidance",
    "unlike", "rather", "instead", "contrast", "contrasted",
    "excluding", "excluded", "deny", "denying", "denies",
    "reject", "rejects", "rejecting",
})

# How many words back to look for a negation
_NEGATION_WINDOW = 8


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_forbidden_claims(invariant: str) -> list[ForbiddenClaim]:
    """
    Parse an invariant string and extract what it forbids.

    Handles:
      "X is not Y"          → forbidden: Y, subject: X
      "X is not a/an Y"     → forbidden: Y, subject: X
      "X does not use Y"    → forbidden: Y (in use-context)
      "not a/an Y"          → forbidden: Y, subject: implicit
    """
    claims: list[ForbiddenClaim] = []
    inv = invariant.strip()

    # Split on sentence boundaries — process each sentence
    sentences = re.split(r'(?<=[.!?])\s+', inv)

    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue

        # "X is/are/am not called Y"  (e.g. "the project is not called ProjectMind")
        m = re.search(
            r'([\w][\w\s\'\-]{1,40}?)\s+(?:is|are|am)\s+not\s+called\s+([\w][\w\s\'\-\.]{2,50})',
            s, re.I,
        )
        if m:
            subject   = _normalise(m.group(1))
            forbidden = _normalise(m.group(2))
            if _worth_checking(forbidden):
                claims.append(ForbiddenClaim(
                    forbidden=forbidden,
                    subject=subject,
                    pattern="is_not",
                    source_text=s,
                ))
            continue

        # "X is/are/am not [a/an] Y"
        m = re.search(
            r'([\w][\w\s\'\-]{1,40}?)\s+(?:is|are|am)\s+not\s+(?:a\s+|an\s+)?([\w][\w\s\'\-\.]{2,50})',
            s, re.I,
        )
        if m:
            subject   = _normalise(m.group(1))
            forbidden = _normalise(m.group(2))
            if _worth_checking(forbidden):
                claims.append(ForbiddenClaim(
                    forbidden=forbidden,
                    subject=subject,
                    pattern="is_not",
                    source_text=s,
                ))
            continue

        # "does/do not <verb> [<prep>] <object>"
        # Broad verb list covers: use, rely on, implement, gate, score, call, contain, etc.
        m = re.search(
            r'(?:does|do)\s+not\s+'
            r'(?:use|rely\s+on|depend\s+on|implement|employ|gate|run|score|'
            r'operate|function|produce|generate|call|support|require|contain|'
            r'include|have|need|allow|accept|return|output|work)\s+'
            r'(?:at\s+|on\s+|with\s+|as\s+|in\s+)?([\w][\w\s\'\-\.\=\%\@\+]{2,50})',
            s, re.I,
        )
        if m:
            forbidden = _normalise(m.group(1))
            if _worth_checking(forbidden):
                claims.append(ForbiddenClaim(
                    forbidden=forbidden,
                    subject="",
                    pattern="does_not_use",
                    source_text=s,
                ))
            continue

        # "not a/an Y" (standalone — no explicit subject)
        m = re.search(
            r'\bnot\s+(?:a\s+|an\s+)([\w][\w\s\'\-\.]{2,40})',
            s, re.I,
        )
        if m:
            forbidden = _normalise(m.group(1))
            if _worth_checking(forbidden):
                claims.append(ForbiddenClaim(
                    forbidden=forbidden,
                    subject="",
                    pattern="is_not",
                    source_text=s,
                ))

    return claims


def _normalise(text: str) -> str:
    """Trim punctuation and excess whitespace from extracted text."""
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    text = text.rstrip('.,!?;:\'"')
    return text.lower()


def _worth_checking(forbidden: str) -> bool:
    """Filter out trivially short or stop-word-only extractions."""
    if len(forbidden) < 4:
        return False
    stopwords = {"this", "that", "there", "here", "what", "which", "when",
                 "just", "only", "very", "some", "much", "more"}
    if forbidden in stopwords:
        return False
    return True


# ── Positive assertion detection ──────────────────────────────────────────────

_NEGATION_WINDOW_AFTER = 6   # trailing negation window (for "X, but Y does not")

def has_positive_assertion(output: str, forbidden: str) -> tuple[bool, str, int]:
    """
    Return (found, evidence_span, position) if `forbidden` appears in `output`
    in a positive (non-negated) assertion context.

    A positive context = the forbidden term appears AND no negation word
    is present within _NEGATION_WINDOW words BEFORE or _NEGATION_WINDOW_AFTER
    words AFTER it.  The trailing window handles "X uses Y, but Z does not."
    """
    pattern = re.compile(re.escape(forbidden), re.I)

    for m in pattern.finditer(output):
        start = m.start()
        end   = m.end()

        # Words before the match
        before_text  = output[:start]
        words_before = re.findall(r'\b\w+\b', before_text)
        nearby_before = {w.lower() for w in words_before[-_NEGATION_WINDOW:]}

        # Words after the match
        after_text  = output[end:]
        words_after = re.findall(r'\b\w+\b', after_text)
        nearby_after = {w.lower() for w in words_after[:_NEGATION_WINDOW_AFTER]}

        if not ((nearby_before | nearby_after) & _NEGATIONS):
            # Grab a short evidence span (± 40 chars)
            evidence_start = max(0, start - 40)
            evidence_end   = min(len(output), end + 40)
            evidence = output[evidence_start:evidence_end].replace('\n', ' ')
            return True, evidence.strip(), start

    return False, "", -1


# ── Checker ───────────────────────────────────────────────────────────────────

class InvariantContradictionChecker:
    """
    Check a candidate output against a list of invariant strings.

    Usage:
        checker = InvariantContradictionChecker()
        contradictions = checker.check(invariants, output_text)
        if contradictions:
            # output must not be emitted as-is
    """

    def check(
        self,
        invariants: list[str],
        output: str,
    ) -> list[ContradictionResult]:
        """
        Return all contradictions found. Empty list = output is safe.
        """
        results: list[ContradictionResult] = []

        for inv in invariants:
            claims = extract_forbidden_claims(inv)
            for claim in claims:
                found, evidence, pos = has_positive_assertion(output, claim.forbidden)
                if found:
                    results.append(ContradictionResult(
                        invariant=inv,
                        forbidden=claim.forbidden,
                        evidence=evidence,
                        position=pos,
                    ))

        return results

    def is_safe(self, invariants: list[str], output: str) -> bool:
        """True if the output contains no invariant contradictions."""
        return len(self.check(invariants, output)) == 0
