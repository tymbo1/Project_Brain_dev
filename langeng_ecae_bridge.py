"""
langeng_ecae_bridge.py — ActivationEngine ↔ LangEng bridge.

Replaces cmsp0/langeng_bridge.py (CMSRouter-based) with the bounded
ActivationEngine + ecae_cache layer.

Flow:
    user text
      → ExpressionRealizer   (emotional/creative/relational)  → expression (fast path)
      → intent_normalizer    (structural queries)              → ResponsePlan
      → extract_subject                                        → subject term
      → ActivationEngine + ecae_cache                         → chains
      → build_payload_from_chains                             → payload dict
      → LanguageRealizer                                       → surface text

Fail-closed at every step — falls back to pure LangEng if ECAE misses.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "projectbrain_dev"))
sys.path.insert(0, str(Path.home() / "Le_P2"))  # Le_P2 takes priority for LangEng modules

from intent_normalizer import normalize_intent, _clean
from response_plans import ResponsePlan
from language_realizer import LanguageRealizer
from expression_realizer import ExpressionRealizer
from turn_context import TurnContext
from capability_context import CapabilityContext
from mode_overlay import ConversationMode
from inference.activation_engine import ActivationEngine
from inference.ecae_cache import get_or_run

# Plans that benefit from CMS retrieval
CMS_PLANS = {
    ResponsePlan.DEFINE,
    ResponsePlan.STATUS,
    ResponsePlan.DESCRIBE,
    ResponsePlan.ELABORATE,
    ResponsePlan.INFORM,
    ResponsePlan.EXPLAIN,
    ResponsePlan.GUIDE,
    ResponsePlan.SUMMARIZE,
}

REFERENCE_WORDS = {"that", "this", "it", "those", "these"}

_PREFIXES = [
    "tell me about ", "what does that mean", "what does this mean",
    "what does it mean", "what is the ", "what are the ",
    "what is ", "what are ", "what was ", "who is ", "who was ",
    "define ", "describe ", "explain ", "summarize ",
    "how does ", "how do ", "why does ", "why do ", "is ", "are ",
]
_STRIP_TRAILING = {" mean", "?", " work", " works", " do", " does", " happen"}
_ARTICLES = {"a ", "an ", "the "}


def _extract_subject(text: str, plan: ResponsePlan) -> str | None:
    t = _clean(text)
    for prefix in _PREFIXES:
        if t.startswith(prefix):
            subject = t[len(prefix):].strip()
            for trail in _STRIP_TRAILING:
                if subject.endswith(trail):
                    subject = subject[:-len(trail)].strip()
            for art in _ARTICLES:
                if subject.startswith(art):
                    subject = subject[len(art):]
                    break
            # Strip residual "how/why" from explain-type extractions
            for lead in ("how ", "why "):
                if subject.startswith(lead):
                    subject = subject[len(lead):]
            if subject and len(subject.split()) <= 6:
                return subject
    tokens = t.split()
    if 1 <= len(tokens) <= 3 and plan in CMS_PLANS:
        return t
    return None


def _parse_chains(chains: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Parse 'subj | pred | obj | strength: N' chains into {pred: [(subj, obj)]}."""
    by_pred: dict[str, list[tuple[str, str]]] = {}
    for chain in chains:
        parts = [p.strip() for p in chain.split("|")]
        if len(parts) >= 3:
            subj, pred, obj = parts[0], parts[1], parts[2]
            by_pred.setdefault(pred, []).append((subj, obj))
    return by_pred


def build_payload_from_chains(plan: ResponsePlan, subject: str,
                               chains: list[str]) -> dict:
    if not chains:
        return {}

    by_pred = _parse_chains(chains)
    focus   = subject.lower()
    lines: list[str] = []

    if plan in (ResponsePlan.DEFINE, ResponsePlan.STATUS):
        is_a     = [obj for s, obj in by_pred.get("is_a", []) if focus in s]
        facet    = [obj for s, obj in by_pred.get("facet_of", []) if focus in s]
        context  = [obj for s, obj in by_pred.get("context_of", []) if focus in s]
        aka      = [obj for s, obj in by_pred.get("also_known_as", []) if focus in s]
        uses     = [obj for s, obj in by_pred.get("uses", []) if focus in s]
        caused   = [s for s, obj in by_pred.get("causes", []) if focus in obj]

        if is_a:
            lines.append(f"{focus.capitalize()} is a type of {', '.join(is_a[:3])}.")
        if facet:
            lines.append(f"It is a facet of {', '.join(facet[:2])}.")
        elif context:
            lines.append(f"It belongs to the domain of {', '.join(context[:2])}.")
        if uses:
            lines.append(f"It uses {', '.join(uses[:3])}.")
        if caused:
            lines.append(f"It is driven by {', '.join(caused[:2])}.")
        if aka:
            lines.append(f"Also known as: {', '.join(aka[:3])}.")

    elif plan in (ResponsePlan.DESCRIBE, ResponsePlan.ELABORATE, ResponsePlan.INFORM):
        for pred, pairs in sorted(by_pred.items()):
            if pred in ("related_to",):
                continue
            related = [obj for s, obj in pairs if focus in s][:4]
            if related:
                verb = pred.replace("_", " ")
                lines.append(f"{focus.capitalize()} {verb} {', '.join(related)}.")

    elif plan in (ResponsePlan.EXPLAIN, ResponsePlan.GUIDE):
        mech_preds = ["causes", "produces", "requires", "enables",
                      "regulates", "inhibits", "activates", "uses", "facet_of"]
        for pred in mech_preds:
            # outbound: focus does X
            related = [obj for s, obj in by_pred.get(pred, []) if focus in s][:3]
            if related:
                verb = pred.replace("_", " ")
                lines.append(f"{focus.capitalize()} {verb} {', '.join(related)}.")
        # inbound mechanistic: what causes/enables focus
        drivers = [s for s, obj in by_pred.get("causes", []) if focus in obj][:2]
        if drivers:
            lines.append(f"It is caused by {', '.join(drivers)}.")

    elif plan == ResponsePlan.SUMMARIZE:
        all_nodes = {obj for pairs in by_pred.values() for _, obj in pairs}
        all_nodes.discard(focus)
        lines.append(f"{focus.capitalize()} is connected to {len(all_nodes)} concepts.")
        for pred, pairs in list(by_pred.items())[:4]:
            targets = [obj for _, obj in pairs if obj != focus][:3]
            if targets:
                lines.append(f"  {pred.replace('_', ' ')}: {', '.join(targets)}")

    if not lines:
        return {}

    return {
        "text":       " ".join(lines),
        "subject":    focus,
        "chain_count": len(chains),
    }


class ECAELangBridge:
    """
    Full pipeline: ExpressionRealizer → ECAE → LangEng.
    Drop-in replacement for Orchestrator for knowledge-heavy queries.
    """

    def __init__(self, capability: CapabilityContext | None = None):
        self.capability = capability or CapabilityContext(
            adult_allowed=False, age_verified=False)
        self.realizer   = LanguageRealizer()
        self.expression = ExpressionRealizer()
        self.context    = TurnContext()
        self.mode       = ConversationMode.GENERAL
        self.engine     = ActivationEngine()

    def handle(self, text: str) -> str:
        # 1. Expression field — emotional/creative/relational fast path
        expr, domain, subtype = self.expression.realize(text, context=self.context)
        if expr:
            self.context.register_expression(domain, subtype)
            return expr
        else:
            self.context.clear_expression()

        # 2. Intent normalization
        plan = normalize_intent(text)

        # 3. Repetition collapse
        tokens = set(text.lower().split())
        refers_back = bool(tokens & REFERENCE_WORDS)
        if self.context.same_turn(plan):
            self.context.register(plan)
            return self.realizer.realize(ResponsePlan.NO_CHANGE, {}, mode=self.mode)

        # 4. ECAE retrieval for knowledge plans
        payload: dict = {}
        if plan in CMS_PLANS:
            subject = _extract_subject(text, plan)
            if subject:
                result = get_or_run(self.engine, subject, max_chains=15)
                chains = result.get("chains", [])
                if chains:
                    payload = build_payload_from_chains(plan, subject, chains)

        self.context.register(plan)

        if payload:
            return self.realizer.realize(ResponsePlan.INFORM, payload, mode=self.mode)
        return self.realizer.realize(plan, {}, mode=self.mode)
