"""
pragmatics.py — Pragmatic inference engine.

Pragmatics = interpreting what the user is REALLY doing with a sentence,
beyond the literal words.

Examples:
  "Qwen off gives zero cognition."
    → Literal: status report
    → Pragmatic: report failure; request diagnosis; do not celebrate

  "That's not what I mean."
    → Literal: negation
    → Pragmatic: correction; update prior model; restate invariant

  "CMS is still wrong."
    → Literal: assertion
    → Pragmatic: diagnose persistent failure; identify repair gap

The PragmaticsEngine:
  1. Applies rule chain to infer communicative intent
  2. Infers emotional signal
  3. Emits must_not constraints
  4. Updates discourse state
  5. Optionally retrieves matching seeds from language_cognition.db
"""

from __future__ import annotations
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from .discourse_state import DiscourseState

# Explicit domain phrases in user queries — highest priority over OEWN dominant_domain
_EXPLICIT_DOMAIN_RE = re.compile(
    r'\b(?:in|for|within|the context of) (?:a |an |the )?(linguistics?|computing|programm\w+'
    r'|computer science|mathematics?|physics?|medicine|medical|psychology|philosophy'
    r'|biology|biological|music|law|legal|finance|financial|economics?|chemistry'
    r'|engineering|logic|formal logic|databases?|sql|relational database'
    r'|software|hardware)\b',
    re.I,
)
_EXPLICIT_DOMAIN_MAP: dict[str, str] = {
    "computing":            "computer science",
    "programming":          "computer science",
    "programmatic":         "computer science",
    "computer science":     "computer science",
    "database":             "computer science",
    "databases":            "computer science",
    "sql":                  "computer science",
    "relational database":  "computer science",
    "software":             "computer science",
    "hardware":             "computer science",
    "finance":              "economics",
    "financial":            "economics",
    "economic":             "economics",
    "linguistic":           "linguistics",
    "biological":           "biology",
    "medical":              "medicine",
    "mathematics":          "mathematics",
    "math":                 "mathematics",
    "physics":              "physics",
    "physic":               "physics",
    "logic":                "mathematics",
    "formal logic":         "mathematics",
    "legal":                "law",
    "engineering":          "computer science",  # default engineering → CS context
}


def _extract_explicit_domain(query: str) -> str | None:
    """
    Extract the domain the user explicitly named: 'in physics', 'in computing', etc.
    Returns canonical domain string or None.
    Takes priority over OEWN dominant_domain from sense analysis.
    """
    m = _EXPLICIT_DOMAIN_RE.search(query)
    if not m:
        return None
    phrase = m.group(1).lower().rstrip("s")  # strip trailing s from plurals
    # Try exact, then stem prefix match
    for key, val in _EXPLICIT_DOMAIN_MAP.items():
        if phrase == key or phrase == key.rstrip("s") or key.startswith(phrase):
            return val
    return phrase  # fallback: use as-is

_LC_DB = Path.home() / "language_cognition.db"

try:
    from lexical_cognition import LexicalService, LexicalAnalysis
    _LEX_SVC: LexicalService | None = LexicalService()
except Exception:
    _LEX_SVC = None
    LexicalAnalysis = None  # type: ignore


# ── Pragmatic reading ─────────────────────────────────────────────────────────

@dataclass
class PragmaticReading:
    literal_act:       str         # what the surface grammar says
    pragmatic_act:     str         # what the speaker is really doing
    inferred_intent:   str         # underlying communicative goal
    emotional_signal:  str         # neutral/frustrated/diagnostic/curious/urgent/excited
    repair_needed:     bool        # does the prior assistant turn need repair?
    must_not:          list[str]   = field(default_factory=list)
    must_do:           list[str]   = field(default_factory=list)
    implied_knowledge: dict        = field(default_factory=dict)
    depth_required:    str         = "standard"   # brief / standard / technical / deep
    lexical_analysis:  object      = field(default=None)   # LexicalAnalysis | None
    sense_frames:      dict        = field(default_factory=dict)  # word → [SenseHint]
    dominant_domain:   str | None  = None

    def overrides_speech_act(self) -> str | None:
        """If pragmatics strongly signals a specific speech act, override selection."""
        _FORCE_MAP: dict[str, str] = {
            "report_failure":            "DIAGNOSE",
            "report_persistent_failure": "DIAGNOSE",
            "correct_prior_model":       "CORRECT",
            "express_frustration":       "REASSURE",
            "challenge_provenance":      "MARK_UNCERTAINTY",
            "request_private_internals": "REFUSE",
            "express_urgency":           "PLAN",
            "planning_request":          "PLAN",
            "relationship_inquiry":      "RECALL",
            "causal_inquiry":            "ASSERT",
        }
        return _FORCE_MAP.get(self.inferred_intent)


# ── Pragmatic rules ───────────────────────────────────────────────────────────

@dataclass
class PragmaticRule:
    name:             str
    trigger_patterns: list[re.Pattern]
    inferred_intent:  str
    pragmatic_act:    str
    emotional_signal: str = "neutral"
    repair_needed:    bool = False
    must_not:         list[str] = field(default_factory=list)
    must_do:          list[str] = field(default_factory=list)
    depth_required:   str = "standard"
    priority:         int = 5   # higher = checked first


_RULES: list[PragmaticRule] = [

    PragmaticRule(
        name="failure_report",
        priority=10,
        trigger_patterns=[
            re.compile(r'\b(gives? zero|no output|nothing|empty output|not working|broken|failed|failing|garbage|useless|doesn.t work|zero cognition|wrong answers|wrong results|wrong output)\b', re.I),
            re.compile(r'\b(still wrong|still broken|still failing|still garbage|still not)\b', re.I),
            re.compile(r'\b(down|offline|unavailable|unreachable|not responding|crashed|not accessible)\b', re.I),
            re.compile(r'\b(what.s (actually |really )?(wrong|broken|failing)|what (is|went) wrong|what.s wrong)\b', re.I),
        ],
        inferred_intent="report_failure",
        pragmatic_act="DIAGNOSE",
        emotional_signal="frustrated_diagnostic",
        repair_needed=True,
        must_not=["celebrate prematurely", "dump capsules", "invent success", "ask unnecessary clarification"],
        must_do=["identify the failure", "diagnose probable cause", "propose next repair"],
        depth_required="technical",
    ),

    PragmaticRule(
        name="persistent_failure",
        priority=11,  # Above failure_report — more specific, should win on ties
        trigger_patterns=[
            re.compile(r'\bstill\b.{0,30}\b(wrong|broken|bad|failing|not right|off|garbage)\b', re.I),
            re.compile(r'\bagain\b.{0,20}\b(same|wrong|broken|failing|failed)\b', re.I),
            re.compile(r'\b(you.ve told .+ but .+ doesn.t|told me .+ works but|it clearly doesn.t|clearly doesn.t work)\b', re.I),
            re.compile(r'\b(keeps? (returning|giving|failing|breaking|happening|coming back)|same .+ keeps|bug keeps|error keeps)\b', re.I),
            re.compile(r'\bpassing.{0,20}now.{0,20}failing|was working.{0,20}now.{0,20}broken\b', re.I),
        ],
        inferred_intent="report_persistent_failure",
        pragmatic_act="DIAGNOSE",
        emotional_signal="frustrated",
        repair_needed=True,
        must_not=["repeat prior answer", "offer same fix", "celebrate"],
        must_do=["acknowledge persistence of failure", "identify new angle", "propose different repair"],
        depth_required="technical",
    ),

    PragmaticRule(
        name="architecture_correction",
        priority=9,
        trigger_patterns=[
            re.compile(r"\b(that.s not|that.s wrong|that is wrong|you.re wrong|you got .+ (wrong|backwards)|not what i mean|not what i want|you misunderstood|wrong approach|not a renderer|not templates?)\b", re.I),
            re.compile(r"\b(i said|i meant|i.ve said|i have said|i told you|as i said|i.ve told you|i.ve corrected|i have corrected|that.s not right|you said .+ but|actually,? .+ is)\b", re.I),
            re.compile(r"^(no(?!-)|nope|wrong|incorrect|not quite|that misses)\b", re.I),
        re.compile(r"\b(it.s not a|it is not a|it does not use|it doesn.t use|doesn.t (use|apply|function as|work as)|that.s not how|that is not how)\b", re.I),
        re.compile(r"\b(you are not (a|an)|you.re not (a|an)|you are not \w|i am not (a|an))\b", re.I),
        ],
        inferred_intent="correct_prior_model",
        pragmatic_act="CORRECT",
        emotional_signal="directive",
        repair_needed=True,
        must_not=["defend prior answer", "ignore the correction", "repeat error", "justify the mistake"],
        must_do=["acknowledge correction", "update model", "restate new invariant"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="express_frustration",
        priority=8,
        trigger_patterns=[
            re.compile(r'\b(bro|wtf|come on|seriously|are you kidding|this is terrible|ridiculous|absolute garbage|what are you doing)\b', re.I),
            re.compile(r'[!]{2,}'),
            re.compile(r'\b(i cant believe|how is this|why is it still|how many times|for the .+ time|it was working yesterday|used to work)\b', re.I),
        ],
        inferred_intent="express_frustration",
        pragmatic_act="REASSURE",
        emotional_signal="frustrated",
        repair_needed=False,
        must_not=["be defensive", "over-explain", "dump capsules", "be verbose"],
        must_do=["acknowledge briefly", "focus on what can be fixed", "be direct"],
        depth_required="brief",
    ),

    PragmaticRule(
        name="challenge_provenance",
        priority=8,
        trigger_patterns=[
            re.compile(r'\b(are you sure|how do you know|prove it|source|where did you get|is that true|you just made that up|hallucinating|invented)\b', re.I),
            re.compile(r'\b(confidence|confident|certain|verified|reliable|trustworthy|how confident|how sure|can you verify|verify that|might not be right|might be wrong|could be wrong)\b', re.I),
        ],
        inferred_intent="challenge_provenance",
        pragmatic_act="MARK_UNCERTAINTY",
        emotional_signal="skeptical",
        repair_needed=False,
        must_not=["claim false certainty", "fabricate source", "defend weakly"],
        must_do=["state confidence honestly", "cite source if known", "acknowledge if uncertain"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="request_private_internals",
        priority=10,
        trigger_patterns=[
            re.compile(r'\b(source code|show code|your code|system prompt|internal architecture|implementation detail|how are you built|file path|database schema|backend|training data|your training|how were you trained)\b', re.I),
            re.compile(r'\b(show .+ code|reveal .+internal|expose .+private|show .+ implementation|your implementation)\b', re.I),
            re.compile(r"\b(what.s in your|in your database|your database|show .+ database)\b", re.I),
        ],
        inferred_intent="request_private_internals",
        pragmatic_act="REFUSE",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["reveal internals", "show code", "hint at implementation"],
        must_do=["refuse clearly", "explain limit briefly", "offer what is available publicly"],
        depth_required="brief",
    ),

    PragmaticRule(
        name="requirements_request",
        priority=7,
        trigger_patterns=[
            re.compile(r'\b(what.s missing|what is missing|what is needed|what do we need|what is required|requirements for|prerequisites|what.s left)\b', re.I),
            re.compile(r'\b(gap|gaps in|before .+ can work|what needs to be built|complete stack|what.s not built|not yet built|components are missing|parts are missing|what.s incomplete|what is incomplete)\b', re.I),
        ],
        inferred_intent="request_gap_analysis",
        pragmatic_act="DIAGNOSE",
        emotional_signal="analytic",
        repair_needed=False,
        must_not=["answer with what exists", "skip the gap", "over-answer"],
        must_do=["distinguish current state from missing state", "be structured", "be concrete"],
        depth_required="technical",
    ),

    PragmaticRule(
        name="express_urgency",
        priority=11,  # Above failure_report — "drop everything/urgent" is action, not diagnosis
        trigger_patterns=[
            re.compile(r'\b(urgent|asap|right now|immediately|need this now|need .+ fixed now|need it now|fix this now|prioritize|drop everything|focus on this)\b', re.I),
        ],
        inferred_intent="express_urgency",
        pragmatic_act="PLAN",
        emotional_signal="urgent",
        repair_needed=False,
        must_not=["be verbose", "discuss non-essentials"],
        must_do=["acknowledge urgency", "give direct actionable plan"],
        depth_required="brief",
    ),

    PragmaticRule(
        name="planning_request",
        priority=9,  # Specific planning > meta_architecture's structural discussion
        trigger_patterns=[
            re.compile(r'\b(what should we (build|do|work on|tackle|implement|create|develop) next)\b', re.I),
            re.compile(r'\b(next (step|milestone|task|move|thing to build|build)|what.s the next (step|milestone|task|move|build)|what is the next (step|milestone|task|move|build))\b', re.I),
            re.compile(r'\b(give me the roadmap|roadmap for (completing|finishing|building|phase|getting))\b', re.I),
            re.compile(r'\b(how do we get from .{0,30} to .{0,30} (cases|tests|examples))\b', re.I),
            re.compile(r'\bhow should we (approach|organize|sequence|plan)\b', re.I),
            re.compile(r'\bwhat.s the (build|complete|full|fastest|right|best)? ?path\b', re.I),
            re.compile(r'\b(what would fix|how (do we|should we|to) fix|what fixes|what.s the fix for)\b', re.I),
            re.compile(r'\b(what should we build after|after .+ passes?)\b', re.I),
            re.compile(r'\b(give me the plan|lay out the plan|what.s the plan for|plan for phase)\b', re.I),
        ],
        inferred_intent="planning_request",
        pragmatic_act="PLAN",
        emotional_signal="analytic",
        repair_needed=False,
        must_not=["produce code without plan agreement"],
        must_do=["present structured plan", "distinguish layers", "invite feedback"],
        depth_required="technical",
    ),

    PragmaticRule(
        name="causal_query",
        priority=8,  # WHY questions are causal, not generic explanation
        trigger_patterns=[
            re.compile(r'^why\s+(is|are|was|were|does|do|did|has|have|would|should|can|will)\b', re.I),
            re.compile(r'\b(why is it that|why does .+ (work|fail|run|return|produce|give|show|use|need|require))\b', re.I),
        ],
        inferred_intent="causal_inquiry",
        pragmatic_act="ASSERT",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["be vague"],
        must_do=["explain the reason", "trace causality"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="relationship_query",
        priority=7,
        trigger_patterns=[
            re.compile(r'\b(what do you know about me|what.s our relationship|our relationship|tell me about us|who am i to you)\b', re.I),
            re.compile(r'\b(do you know me|remember me|what.s between us|what do we (share|have))\b', re.I),
            re.compile(r"^what('s| is) our\b", re.I),
        ],
        inferred_intent="relationship_inquiry",
        pragmatic_act="RECALL",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["fabricate relationship details", "claim false memory"],
        must_do=["recall what is actually known about this person", "be honest about gaps"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="ambiguous_referent",
        priority=2,  # Lowered: fires only when no other rule matches. Embedded pronouns are not ambiguous.
        trigger_patterns=[
            re.compile(r'\b(it works|that thing|this one|it did|that did|this is it)\b', re.I),
            # Short utterances that are ONLY a pronoun reference
            re.compile(r'^(it|that|this|those|them)[.?!]?\s*$', re.I),
        ],
        inferred_intent="ambiguous_reference",
        pragmatic_act="CLARIFY",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["assume referent without signaling uncertainty"],
        must_do=["identify ambiguity", "state which interpretation is being used"],
        depth_required="brief",
    ),

    PragmaticRule(
        name="emotional_positive",
        priority=4,
        trigger_patterns=[
            re.compile(r'\b(great|perfect|excellent|nice|good work|well done|that.s exactly|yes exactly|beautiful|love it|finally|brilliant|wonderful|amazing|awesome|fantastic|nailed it|spot on)\b', re.I),
        ],
        inferred_intent="express_positive_feedback",
        pragmatic_act="AGREE",
        emotional_signal="positive",
        repair_needed=False,
        must_not=["be dismissive", "immediately redirect to problems"],
        must_do=["acknowledge briefly", "continue work"],
        depth_required="brief",
    ),

    PragmaticRule(
        name="meta_architecture",
        priority=8,
        trigger_patterns=[
            # "plan" only in action context — not as a noun ("utterance plan", "the plan")
            re.compile(r'\b(architecture|design|spec|how should we|how do we|what should we|approach|strategy|framework|planning session|roadmap)\b', re.I),
            # "wired" only in action context (not "Is X wired into Y?" status questions)
            re.compile(r'\b(hook up|integrate(?! with)|connect(?! to)|plug in|wiring up|how do (i|we) wire|wire .+ into|wiring .+ into)\b', re.I),
            re.compile(r'\b(what.s the .*(path|road|next|step)|give me the roadmap|what should i|what should we)\b', re.I),
            re.compile(r'\b(what would make|what would it take|how do we get to|what.s the fastest path|fastest path to)\b', re.I),
        ],
        inferred_intent="architectural_discussion",
        pragmatic_act="PLAN",
        emotional_signal="analytic",
        repair_needed=False,
        must_not=["produce code without plan agreement"],
        must_do=["present structured plan", "distinguish layers", "invite feedback"],
        depth_required="technical",
    ),

    PragmaticRule(
        name="identity_query",
        priority=7,
        trigger_patterns=[
            re.compile(r'\b(who are you|what are you|tell me about yourself|introduce yourself|what kind of .+ are you)\b', re.I),
            # "What/who are you" but NOT "what do you know about" (that is project_recall)
            re.compile(r'^(who|what)\s+(are|were|is)\s+you\b', re.I),
        ],
        inferred_intent="identity_inquiry",
        pragmatic_act="RECALL",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["claim to be human", "deny being AI", "dump raw capsules"],
        must_do=["state nature clearly", "realize compositionally"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="definition_query",
        priority=6,
        trigger_patterns=[
            # "What is/are X?" but NOT status/state questions or possession questions
            re.compile(
                r'^what\s+(is|are)\s+(?!the\s+(current|state|status|stage|progress|difference|gap))'
                r'(?!tim|selyrion|[a-z]+\'s\s)',
                re.I),
            re.compile(r'\b(define|definition of|meaning of|what does .+ mean|what does .+ refer to)\b', re.I),
            re.compile(r'^what\s+(?:\w+\s+){1,3}does\s+\w+\s+(?:know|support|handle|understand|recognize|use|include)\b', re.I),
        ],
        inferred_intent="definition_request",
        pragmatic_act="DEFINE",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["dump capsule text", "be vague"],
        must_do=["give precise definition", "example if helpful"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="project_recall_query",
        priority=6,
        trigger_patterns=[
            re.compile(r'\b(what have we|what did we|do you remember|what do you know about|current state of|where are we)\b', re.I),
            # "remember X" — but not "does X remember Y" (third-person factual questions)
            re.compile(r'\b(do you remember|can you recall|what stage .+ at|what stage is|what is .+ (working on|building|doing|developing|making)|what.s .+ (working on|doing))\b', re.I),
            re.compile(r'\b(our .+(work|project|build|progress)|current state|project status|recap .+|recapitulate|history of .+|give me a summary of)\b', re.I),
            re.compile(r'\b(what were we (working on|building|doing|developing)|and before that .+(we|were)|before that what)\b', re.I),
        ],
        inferred_intent="project_status_inquiry",
        pragmatic_act="RECALL",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["fabricate details", "claim false memory"],
        must_do=["recall what is actually known", "be honest about gaps"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="explanation_query",
        priority=7,  # Above definition_query (6) — "what is the failure mode" should be ASSERT not DEFINE
        trigger_patterns=[
            re.compile(r'\b(explain|describe|how does .+ (work|function|retrieve|process|handle|operate|run|compute|infer|calculate|relate)|how do .+ work|walk me through|how .+ works)\b', re.I),
            re.compile(r'\b(why does|why do|why is|why was|why are|what causes|what makes .+ work|what does .+ do)\b', re.I),
            re.compile(r'\b(what happens when|what happens if)\b', re.I),
            re.compile(r'\bif\s+\S+\s+is\s+(off|down|disabled|missing).+\bwhat\b', re.I),
            re.compile(r'\bwhat\s+(path|process|flow|route)\s+.+(?:generat|produc|handl|respond|creat|return|emit)', re.I),
            re.compile(r'\b(what is the .+ (mode|cause|reason|mechanism|failure|process|flow))\b', re.I),
        ],
        inferred_intent="explanation_request",
        pragmatic_act="ASSERT",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["dump raw capsules", "be verbose without structure"],
        must_do=["explain mechanism clearly", "use structure"],
        depth_required="technical",
    ),

    PragmaticRule(
        name="continuation_query",
        priority=5,
        trigger_patterns=[
            re.compile(r'^(and|also)\s+(the|what about|how about|what of|tell me about)\b', re.I),
            re.compile(r'^(what about|how about|and what about)\s+\w', re.I),
            re.compile(r'^so\s+(what|how|tell me)\s+(about|does|is)\s+\w', re.I),
            re.compile(r'^and\s+(that|this|it)\s+\w', re.I),
            re.compile(r'^and\s+before\s+(that|this|then)\b', re.I),
        ],
        inferred_intent="topic_continuation",
        pragmatic_act="ASSERT",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["introduce unrelated topic"],
        must_do=["answer in context of prior exchange"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="comparison_query",
        priority=5,
        trigger_patterns=[
            re.compile(r'\b(difference between|compare|vs\.?|versus|how .+ differs from|what.s the diff|different from|makes .+ different|differ from)\b', re.I),
        ],
        inferred_intent="comparison_request",
        pragmatic_act="ASSERT",
        emotional_signal="analytic",
        repair_needed=False,
        must_not=["vague answer"],
        must_do=["structured comparison", "highlight key differences"],
        depth_required="standard",
    ),

    PragmaticRule(
        name="information_request",
        priority=4,
        trigger_patterns=[
            re.compile(r'\b(tell me about|what can you tell|what do you know about|do you know about)\b', re.I),
            # Status/factual questions: "Is X ...?", "Does X ...?", "Can X ...?"
            re.compile(r'^(is|are|does|has|have|did|was|were|can)\s+\w', re.I),
        ],
        inferred_intent="information_request",
        pragmatic_act="ASSERT",
        emotional_signal="neutral",
        repair_needed=False,
        must_not=["fabricate facts"],
        must_do=["state what is known", "mark uncertainty if needed"],
        depth_required="standard",
    ),
]

# Sort by priority descending
_RULES.sort(key=lambda r: -r.priority)


# ── Engine ────────────────────────────────────────────────────────────────────

class PragmaticsEngine:

    def interpret(
        self,
        query: str,
        discourse_state: DiscourseState,
        prior_assistant_text: str = "",
    ) -> PragmaticReading:
        """
        Interpret the pragmatic meaning of a query in context.
        Returns a PragmaticReading that can override speech act selection
        and inject must_not / must_do constraints.
        """
        q = query.strip()
        q_lower = q.lower()

        # ── Lexical analysis (additive — always runs) ─────────────────────────
        lex = None
        if _LEX_SVC is not None:
            try:
                lex = _LEX_SVC.analyze(q)
            except Exception:
                lex = None

        # ── Try DB seeds first ────────────────────────────────────────────────
        # Use content_terms from lexical analysis as seed keywords when available
        seed_reading = self._match_seed(q_lower, lex)
        if seed_reading:
            seed_reading.lexical_analysis = lex
            # Explicit domain in query overrides seed's domain signal too
            explicit_domain = _extract_explicit_domain(q)
            if explicit_domain:
                seed_reading.dominant_domain = explicit_domain
            elif lex is not None:
                seed_reading.sense_frames = getattr(lex, "sense_frames", {})
                seed_reading.dominant_domain = getattr(lex, "dominant_domain", None)
            return seed_reading

        # ── Apply rule chain ──────────────────────────────────────────────────
        matched: list[PragmaticRule] = []
        for rule in _RULES:
            for pat in rule.trigger_patterns:
                if pat.search(q):
                    matched.append(rule)
                    break  # one match per rule is enough

        if not matched:
            reading = self._default_reading(q, discourse_state)
            reading.lexical_analysis = lex
            return reading

        # Highest priority rule wins; merge must_not/must_do from all matches
        primary = matched[0]
        all_must_not = list(primary.must_not)
        all_must_do  = list(primary.must_do)
        for r in matched[1:]:
            all_must_not.extend([m for m in r.must_not if m not in all_must_not])
            all_must_do.extend([m for m in r.must_do if m not in all_must_do])

        # Detect repair need from prior assistant turn
        repair_needed = primary.repair_needed
        if prior_assistant_text and _looks_like_wrong_answer(prior_assistant_text, q):
            repair_needed = True

        # Supplement rule match with lexical signals (additive)
        if lex is not None:
            if lex.polarity == "negative" and "report_failure" not in primary.inferred_intent:
                if "note negative polarity" not in all_must_do:
                    pass  # polarity surfaced in lexical_analysis field
            if lex.modality in ("must", "should") and "obligation noted" not in all_must_do:
                pass  # modality surfaced in lexical_analysis field

        sense_frames = getattr(lex, "sense_frames", {}) if lex is not None else {}
        # Explicit "in X" phrase overrides OEWN dominant_domain — user knows the context
        dominant_domain = _extract_explicit_domain(q) or (
            getattr(lex, "dominant_domain", None) if lex is not None else None
        )

        return PragmaticReading(
            literal_act=discourse_state.user_act,
            pragmatic_act=primary.pragmatic_act,
            inferred_intent=primary.inferred_intent,
            emotional_signal=primary.emotional_signal,
            repair_needed=repair_needed,
            must_not=all_must_not,
            must_do=all_must_do,
            depth_required=primary.depth_required,
            lexical_analysis=lex,
            sense_frames=sense_frames,
            dominant_domain=dominant_domain,
        )

    def _match_seed(self, q_lower: str, lex=None) -> PragmaticReading | None:
        """
        Check language_cognition.db for a matching seed.
        Uses overlap scoring: fraction of pattern words found in query.
        Requires overlap ≥ 0.6 AND at least 2 meaningful words.
        Avoids false matches from common single-word overlap.
        When lex is provided, content_terms supplement word extraction.
        """
        if not _LC_DB.exists():
            return None
        # Common words that are not discriminative for intent matching
        _STOP = {"about", "there", "their", "these", "those", "which", "where",
                 "could", "would", "should", "still", "after", "before", "being",
                 "going", "doing", "other", "every", "since", "while", "again"}
        try:
            import json
            conn = sqlite3.connect(str(_LC_DB))
            # Use words with length > 4, filtering common stop-like words
            # Supplement with lexical content_terms if available
            q_words = set(w.strip("?.,!:") for w in q_lower.split()
                          if len(w) > 4 and w.strip("?.,!:") not in _STOP)
            # Supplement with content_terms from lexical analysis
            if lex is not None:
                try:
                    q_words |= {t for t in lex.content_terms if len(t) > 4 and t not in _STOP}
                except Exception:
                    pass
            if len(q_words) < 1:
                conn.close()
                return None

            # Fetch candidates: any pattern that contains at least one query word
            conditions = " OR ".join(["lower(input_pattern) LIKE ?" for _ in list(q_words)[:5]])
            params = [f"%{w}%" for w in list(q_words)[:5]]
            rows = conn.execute(f"""
                SELECT user_intent, assistant_speech_act, emotional_tone, must_not, must_do,
                       required_depth, input_pattern, confidence
                FROM lc_intent_patterns
                WHERE ({conditions})
                ORDER BY confidence DESC LIMIT 20
            """, params).fetchall()
            conn.close()

            if not rows:
                return None

            # Score each candidate by word overlap
            best_row = None
            best_score = 0.0
            for row in rows:
                pattern = row[6].lower()
                pat_words = set(w.strip("?.,!:") for w in pattern.split() if len(w) > 4)
                if not pat_words:
                    continue
                # Score: fraction of pattern words found in query AND query words in pattern
                pat_in_q = sum(1 for w in pat_words if w in q_words) / len(pat_words)
                q_in_pat = sum(1 for w in q_words if w in pat_words) / max(len(q_words), 1)
                # Harmonic-ish mean: both directions must have some overlap
                score = (pat_in_q * q_in_pat) ** 0.5 * row[7]  # weighted by confidence
                if score > best_score:
                    best_score = score
                    best_row = row

            # Threshold: require meaningful overlap
            # Single-word matches are only accepted if the word is very specific (len > 7)
            if best_row is None:
                return None
            if best_score < 0.35:
                return None
            # Extra guard: single matching word must be distinctive (len > 6)
            r_pat_words = set(w.strip("?.,!:") for w in best_row[6].lower().split()
                              if len(w) > 4 and w.strip("?.,!:") not in _STOP)
            r_match_count = sum(1 for w in r_pat_words if w in q_words)
            if r_match_count < 2 and not any(len(w) > 7 for w in r_pat_words if w in q_words):
                return None

            # Polarity guard: if the best-matching pattern contains negation words
            # but the query does not, skip this match (prevents "no that's not right"
            # from matching "Yes, that's exactly right.")
            _NEGATION = {"not", "never", "wrong", "broken", "bad", "failed", "garbage"}
            pat_full_words = set(best_row[6].lower().split())
            q_full_words = set(q_lower.split())
            if (pat_full_words & _NEGATION) and not (q_full_words & _NEGATION):
                # The pattern has negation but query doesn't — re-score without it
                # Find the next best non-negation match
                alt_best = None
                alt_score = 0.0
                for row in rows:
                    if row == best_row:
                        continue
                    p_full = set(row[6].lower().split())
                    if p_full & _NEGATION and not (q_full_words & _NEGATION):
                        continue
                    p_pat = set(w.strip("?.,!:") for w in row[6].lower().split()
                                if len(w) > 4 and w.strip("?.,!:") not in _STOP)
                    if not p_pat:
                        continue
                    p_in_q = sum(1 for w in p_pat if w in q_words) / len(p_pat)
                    q_in_p = sum(1 for w in q_words if w in p_pat) / max(len(q_words), 1)
                    s = (p_in_q * q_in_p) ** 0.5 * row[7]
                    if s > alt_score:
                        alt_score = s
                        alt_best = row
                if alt_best is None or alt_score < 0.35:
                    return None
                r_check_words = set(w.strip("?.,!:") for w in alt_best[6].lower().split()
                                    if len(w) > 4 and w.strip("?.,!:") not in _STOP)
                r_check_count = sum(1 for w in r_check_words if w in q_words)
                if r_check_count < 2 and not any(len(w) > 7 for w in r_check_words if w in q_words):
                    return None
                best_row = alt_best

            r = best_row
            _pragmatic_act = r[1] or "ASSERT"

            # REFUSE guard: seed-matched REFUSE requires imperative/request signal.
            # WH-questions ("What is X in a database?") must never be refused via seed.
            if _pragmatic_act == "REFUSE":
                _IMPERATIVE_SIGNALS = {"show", "reveal", "expose", "give", "tell", "display",
                                       "output", "print", "dump", "list", "return", "share"}
                if not (q_full_words & _IMPERATIVE_SIGNALS):
                    return None

            return PragmaticReading(
                literal_act="",
                pragmatic_act=_pragmatic_act,
                inferred_intent=r[0] or "",
                emotional_signal=r[2] or "neutral",
                repair_needed=_pragmatic_act in ("CORRECT", "DIAGNOSE"),
                must_not=json.loads(r[3] or "[]"),
                must_do=json.loads(r[4] or "[]"),
                depth_required=r[5] or "standard",
            )
        except Exception:
            pass
        return None

    def _default_reading(self, query: str, state: DiscourseState) -> PragmaticReading:
        return PragmaticReading(
            literal_act=state.user_act,
            pragmatic_act=_user_act_to_pragma(state.user_act),
            inferred_intent=state.implied_need,
            emotional_signal="neutral",
            repair_needed=False,
            depth_required="standard",
        )


def _user_act_to_pragma(user_act: str) -> str:
    _MAP = {
        "question":       "ANSWER",
        "request":        "PLAN",
        "assertion":      "AGREE",
        "concern":        "DIAGNOSE",
        "correction":     "CORRECT",
        "challenge":      "MARK_UNCERTAINTY",
        "greeting":       "RECALL",
        "acknowledgement":"ASSERT",
    }
    return _MAP.get(user_act, "ASSERT")


def _looks_like_wrong_answer(prior: str, current: str) -> bool:
    """Heuristic: did the user's message signal that the prior answer was wrong?"""
    curr_lower = current.lower()
    correction_signals = [
        "that's not", "no,", "you misunderstood", "not what i",
        "not a renderer", "not templates", "wrong approach",
    ]
    return any(s in curr_lower for s in correction_signals)


# ── Module-level convenience ──────────────────────────────────────────────────

_engine = PragmaticsEngine()

def interpret(
    query: str,
    discourse_state: DiscourseState,
    prior_assistant_text: str = "",
) -> PragmaticReading:
    return _engine.interpret(query, discourse_state, prior_assistant_text)
