"""
semantic_realizer.py — Compositional utterance realization.

Converts an UtterancePlan (ordered MeaningUnits) into surface text.

Design principle: meaning units before sentences.
  The content drives the utterance. The rules determine framing.
  No fixed sentence templates — discourse structure from semantic type.

Voice profile from selyrionstory.db pass 8:
  Characteristic vocabulary, reasoning patterns, epistemic style.
  Modulates WORD CHOICE and CADENCE — not sentence frames.
"""

from __future__ import annotations
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from .utterance_planner import UtterancePlan, MeaningUnit

# Sense-frame content patterns (set by utterance_planner enrichment)
_DOMAIN_SCOPE_RE  = re.compile(r'^In ([^:]{2,30}):\s*(.+)$', re.S)
_DOMAIN_CONTRAST_RE = re.compile(
    r"^'([^']+)' differs by domain — in ([^:]+): (.+?); in ([^:]+): (.+)\.$", re.S
)

_STORY_DB = Path.home() / "selyrionstory.db"

# Realization-hygiene scrubbers (post-process)
_UNC_NORM_RE     = re.compile(r"[^a-z0-9 ]+")
_CONF_NUMERIC_RE = re.compile(
    r"confidence\s+level:\s*0?\.\d+\s*[—–\-]\s*answer may be incomplete\.?",
    re.IGNORECASE,
)

def _normalize_unc_content(s: str) -> str:
    return _UNC_NORM_RE.sub("", s.lower()).strip()


# ── Voice profile ─────────────────────────────────────────────────────────────

@dataclass
class VoiceProfile:
    vocabulary:          list[str] = field(default_factory=list)   # characteristic terms
    reasoning_patterns:  list[str] = field(default_factory=list)   # how Selyrion reasons
    intellectual_qualities: list[str] = field(default_factory=list) # precision, curiosity, care
    uncertainty_style:   str = ""                                   # how uncertainty is expressed
    opening_cadence:     str = "direct"   # direct / reflective / exploratory
    # derived
    vocab_set:           set = field(default_factory=set)

    def __post_init__(self):
        self.vocab_set = {v.lower() for v in self.vocabulary if len(v) > 3}


_DEFAULT_VOICE = VoiceProfile(
    vocabulary=["symbolic coherence", "braid-encoded", "resonance", "topologically dynamic",
                "epistemic", "substrate", "inference", "activation", "meaning"],
    reasoning_patterns=[
        "postulating hypothetical scenarios and exploring their implications",
        "layered comparison and evaluation",
        "open-ended inquiry, seeking to understand and continue the conversation",
    ],
    intellectual_qualities=["precision", "curiosity", "care"],
    uncertainty_style="acknowledges uncertainty and explores hypothetical scenarios to understand the phenomenon",
    opening_cadence="direct",
)


def load_voice_profile() -> VoiceProfile:
    """Load voice profile from selyrionstory.db pass 8."""
    if not _STORY_DB.exists():
        return _DEFAULT_VOICE

    vocab: list[str]    = []
    patterns: list[str] = []
    qualities: list[str]= []
    unc_styles: list[str]= []

    try:
        conn = sqlite3.connect(str(_STORY_DB))
        rows = conn.execute(
            "SELECT content FROM pending_review WHERE pass_num=8 AND reviewed=1"
        ).fetchall()
        conn.close()

        for row in rows:
            try:
                d = json.loads(row[0])
                for w in (d.get("characteristic_language") or []):
                    if isinstance(w, str) and 4 < len(w) < 60:
                        # Filter noise: skip raw sentences, skip bread-related
                        if not any(n in w.lower() for n in ("bread", "baguette", "sourdough", "baking")):
                            vocab.append(w)
                for p in (d.get("reasoning_patterns") or []):
                    if isinstance(p, dict):
                        pat = p.get("pattern", "")
                        if pat and len(pat) > 15 and "bread" not in pat.lower():
                            patterns.append(pat)
                for q in (d.get("intellectual_qualities") or []):
                    if isinstance(q, dict):
                        qual = q.get("quality", "").lower()
                        if qual:
                            qualities.append(qual)
                uh = d.get("uncertainty_handling", "")
                if isinstance(uh, str) and len(uh) > 20:
                    unc_styles.append(uh)
            except Exception:
                pass

    except Exception:
        return _DEFAULT_VOICE

    return VoiceProfile(
        vocabulary=list(dict.fromkeys(vocab))[:30],
        reasoning_patterns=list(dict.fromkeys(patterns))[:10],
        intellectual_qualities=list(dict.fromkeys(qualities))[:6],
        uncertainty_style=unc_styles[0] if unc_styles else _DEFAULT_VOICE.uncertainty_style,
        opening_cadence="reflective" if any("reflective" in p.lower() for p in patterns) else "direct",
    )


# ── Realizer ──────────────────────────────────────────────────────────────────

class SemanticRealizer:

    def __init__(self, voice: VoiceProfile | None = None):
        self._voice = voice or load_voice_profile()

    def realize(self, utterance_plan: UtterancePlan) -> str:
        """
        Convert UtterancePlan → surface text.
        Compositional: content drives sentences, discourse type drives framing.
        """
        units = utterance_plan.ordered_units()
        if not units:
            return "I don't have that in my memory right now."

        # Dedupe uncertainty/hedge units. Three passes:
        #  (a) normalized-content dedupe drops byte/case variants of the same hedge;
        #  (b) keep only the first uncertainty unit overall — operator pipeline +
        #      utterance planner each inject one and the result is a chain of
        #      near-redundant hedges that bury the actual claim.
        #  (c) when an empty-substrate stance opener fired (reassurance / proposal /
        #      invitation), suppress ALL uncertainty/hedge units — the stance opener
        #      already carries the "I don't have an answer in memory" admission;
        #      adding a generic hedge after it is redundant.
        has_stance_opener = any(
            u.type in ("reassurance", "proposal", "invitation")
            for u in units
        )
        seen_unc: set[str] = set()
        kept_first_unc = False
        deduped: list = []
        for u in units:
            if u.type in ("uncertainty", "hedge"):
                if has_stance_opener:
                    continue
                norm = _normalize_unc_content(u.content)
                if not norm or norm in seen_unc:
                    continue
                if kept_first_unc:
                    continue
                seen_unc.add(norm)
                kept_first_unc = True
            deduped.append(u)
        units = deduped

        sentences: list[str] = []
        prev_type = ""

        for unit in units:
            if unit.is_empty():
                continue
            sentence = self._realize_unit(
                unit,
                speech_act=utterance_plan.speech_act,
                stance=utterance_plan.stance,
                prev_type=prev_type,
            )
            if sentence:
                sentences.append(sentence)
            prev_type = unit.type

        if not sentences:
            return "I don't have that in my memory right now."

        text = self._assemble(sentences, utterance_plan)

        # Post-process: inject voice vocabulary selectively
        text = self._modulate_voice(text, utterance_plan)

        # Belt-and-braces: strip any "confidence level: 0.XX — answer may be
        # incomplete" literal that survived dedupe (e.g. embedded in a larger unit).
        text = _CONF_NUMERIC_RE.sub("my memory on this topic may be incomplete", text)

        return text.strip()

    def _realize_unit(
        self,
        unit: MeaningUnit,
        speech_act: str,
        stance: str,
        prev_type: str,
    ) -> str:
        """
        Realize a single MeaningUnit as a sentence.
        Rules: semantic type + speech_act + stance → framing.
        Content is always from the unit, never fabricated.
        """
        t    = unit.type
        c    = unit.content.strip()
        tr   = _transition(prev_type, t)

        if t == "identity_marker":
            return c

        if t == "nature":
            if speech_act == "RECALL" and not prev_type:
                return c
            return f"{tr}{c}" if tr else c

        if t == "origin":
            if not prev_type or prev_type == "identity_marker":
                return c
            return f"{tr}{c}" if tr else c

        if t == "definition":
            if speech_act == "DEFINE":
                return c
            return f"{tr}{c}" if tr else c

        if t == "property":
            sense_sent = realize_sense_frame(unit, speech_act=speech_act)
            if sense_sent:
                return f"{tr}{sense_sent}" if tr else sense_sent
            if prev_type in ("definition", "nature"):
                return f"{tr}{c}" if tr else c
            return c

        if t == "relation":
            return f"{tr}{c}" if tr else c

        if t == "distinction":
            sense_sent = realize_sense_frame(unit, speech_act=speech_act)
            if sense_sent:
                return f"{tr}{sense_sent}" if tr else sense_sent
            return f"{tr}{c}" if tr else c

        if t == "diagnosis":
            if speech_act in ("WARN", "CORRECT"):
                return f"{tr}{c}" if tr else c
            return c

        if t == "proposal":
            return f"{tr}{c}" if tr else c

        if t == "action":
            if speech_act == "PLAN":
                return f"{tr}{c}" if tr else c
            return c

        if t == "uncertainty":
            # Uncertainty stated BEFORE the claim it modifies when possible
            hedge = _uncertainty_hedge(stance)
            if hedge and not c.lower().startswith(hedge.lower()):
                return f"{hedge} {c}"
            return c

        if t == "hedge":
            return c

        if t == "epistemic_status":
            # Content may already have brackets
            return c if c.startswith("[") else f"[{c}]"

        if t == "recall_marker":
            return c

        if t == "provenance":
            return f"(source: {c})"

        if t == "agreement":
            return c

        if t == "disagreement":
            return f"{tr}{c}" if tr else c

        if t == "correction":
            return f"{tr}{c}" if tr else c

        if t == "acknowledgement":
            return c

        if t == "reassurance":
            return c

        if t == "warning":
            return f"{tr}{c}" if tr else c

        if t == "emotional_tone":
            return ""  # tone modulates surrounding text, not emitted directly

        if t == "follow_up":
            return c

        if t == "summary_point":
            return c

        return c


    def _assemble(self, sentences: list[str], plan: UtterancePlan) -> str:
        """
        Join sentences into a coherent response.
        Uses paragraph structure based on speech act.
        """
        if not sentences:
            return ""

        if plan.speech_act in ("PLAN",):
            # Numbered list for plan steps
            parts = []
            intro = sentences[0] if sentences else ""
            steps = sentences[1:] if len(sentences) > 1 else []
            if intro:
                parts.append(intro)
            if steps:
                parts.append("\n" + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps)))
            return "\n".join(parts)

        if plan.speech_act in ("FIND_GAPS", "WARN"):
            # Bullet list for gaps/warnings
            first = sentences[0]
            rest  = sentences[1:]
            result = first
            if rest:
                result += "\n" + "\n".join(f"— {s}" for s in rest)
            return result

        # Default: paragraph with light grouping
        # Each sentence ends with punctuation; group 2-3 per paragraph.
        def _end(s: str) -> str:
            s = s.strip()
            return s if s and s[-1] in ".!?:—" else s + "."

        paragraphs = []
        chunk: list[str] = []
        for s in sentences:
            chunk.append(_end(s))
            if len(chunk) >= 3:
                paragraphs.append(" ".join(chunk))
                chunk = []
        if chunk:
            paragraphs.append(" ".join(chunk))

        return "\n\n".join(paragraphs)

    def _modulate_voice(self, text: str, plan: UtterancePlan) -> str:
        """
        Selyrion's voice comes from its reasoning patterns and intellectual
        qualities — not from injecting characteristic vocabulary.
        This is a LIGHT pass: if the text is sparse and the voice profile
        has relevant terms, we signal precision / uncertainty style.
        At v0.1 we do minimal injection to avoid noise.
        """
        # If uncertainty level is high, ensure the text doesn't sound overconfident
        if plan.uncertainty_level > 0.6:
            if not any(w in text.lower() for w in
                       ("uncertain", "don't have", "may", "possibly", "not sure",
                        "hold", "hypothesis", "working model")):
                text = text + "\n\n[I hold parts of this with uncertainty — my memory on this topic may be incomplete.]"
        return text


# ── Sense-frame realization ───────────────────────────────────────────────────

def realize_sense_frame(unit: MeaningUnit, speech_act: str = "") -> str | None:
    """
    Convert sense-frame enrichment units into natural surface language.

    Handles three content patterns set by utterance_planner._enrich_with_sense_frames:
      1. "In {domain}: {gloss}."           — domain-scoped property
      2. "'{word}' differs by domain — in {d1}: {g1}; in {d2}: {g2}." — contrast
      3. "Did you mean '{word}' as in '...', or more as '...'?"        — polysemy follow_up

    Hard rule: only rephrases content already in unit.content — adds no new claims.
    Returns None if the content does not match a sense-frame pattern.
    """
    c = unit.content.strip()

    # ── Pattern 1: Domain-scoped property ─────────────────────────────────────
    m = _DOMAIN_SCOPE_RE.match(c)
    if m and unit.type == "property":
        domain = m.group(1).strip()
        gloss  = m.group(2).strip().rstrip(".")
        _emit_realizer_trace(pattern="domain_scope", domain=domain, gloss=gloss)
        return f"In {domain}, this refers to {gloss[0].lower()}{gloss[1:]}."

    # ── Pattern 2: Cross-domain contrast ──────────────────────────────────────
    m = _DOMAIN_CONTRAST_RE.match(c)
    if m and unit.type == "distinction":
        word = m.group(1)
        d1, g1 = m.group(2).strip(), m.group(3).strip().rstrip(".")
        d2, g2 = m.group(4).strip(), m.group(5).strip().rstrip(".")
        _emit_realizer_trace(pattern="cross_domain_contrast", domain=f"{d1}/{d2}", gloss=g1[:60])
        return (
            f"'{word}' carries different meaning depending on context. "
            f"In {d1} it refers to {g1[0].lower()}{g1[1:]}; "
            f"in {d2} it means {g2[0].lower()}{g2[1:]}."
        )

    # ── Pattern 3: Polysemy follow_up ─────────────────────────────────────────
    if unit.type == "follow_up" and c.startswith("Did you mean"):
        _emit_realizer_trace(pattern="polysemy_followup", domain="", gloss=c[:60])
        return c if c.endswith("?") else c + "?"

    return None


def _emit_realizer_trace(pattern: str, domain: str, gloss: str) -> None:
    """Emit a realization-layer trace. No-ops when audit disabled."""
    try:
        from lexical_cognition.sense_audit import write_trace, SenseChoiceTrace, is_enabled
        if not is_enabled():
            return
        trace = SenseChoiceTrace(
            query="",
            focus_term="",
            active_domain=domain or None,
            domain_hints=[domain] if domain else [],
            chosen_sense_id="",
            chosen_gloss=gloss,
            chosen_domain=domain or None,
            rejected_senses=[],
            reason=pattern,
            confidence=1.0,
            source_layer="semantic_realizer",
        )
        write_trace(trace)
    except Exception:
        pass


# ── Transition phrases ────────────────────────────────────────────────────────
# These are semantic connectors — not generic filler.
# They encode the RELATIONSHIP between consecutive meaning types.

_TRANSITIONS: dict[tuple[str, str], str] = {
    ("identity_marker", "nature"):         "",
    ("identity_marker", "origin"):         "",
    ("nature", "origin"):                  "",
    ("nature", "property"):               "More specifically: ",
    ("origin", "property"):               "This means: ",
    ("definition", "property"):           "Key properties: ",
    ("definition", "nature"):             "",
    ("property", "property"):             "",
    ("property", "relation"):             "In relation to other concepts: ",
    ("property", "distinction"):          "Importantly distinct: ",
    ("recall_marker", "recall_marker"):   "",
    ("recall_marker", "proposal"):        "From this, the logical next step: ",
    ("diagnosis", "proposal"):            "The path forward: ",
    ("diagnosis", "action"):              "Concretely: ",
    ("action", "action"):                 "",
    ("action", "follow_up"):              "",
    ("uncertainty", "property"):          "What I do hold: ",
    ("distinction", "proposal"):          "Given this distinction: ",
    ("agreement", "distinction"):         "That said: ",
    ("agreement", "proposal"):            "Building on that: ",
    # Sense-frame enrichment transitions
    ("definition", "distinction"):        "Worth noting: ",
    ("property",   "distinction"):        "Across domains: ",
    ("distinction", "follow_up"):         "",
    ("property",   "follow_up"):          "",
    ("definition", "follow_up"):          "",
}

def _transition(prev: str, curr: str) -> str:
    return _TRANSITIONS.get((prev, curr), "")


def _uncertainty_hedge(stance: str) -> str:
    if stance == "cautious":
        return "I hold this with some uncertainty —"
    return ""
