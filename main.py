#!/usr/bin/env python3
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from symbolic_core.core_api import core
from inference.activation_engine import ActivationEngine
from memory.memory_core import store_triple
from nl_parser import parse_nat, parse_rel
from langeng_bridge import chains_to_prose as synthesize
from llm_articulator import articulate, is_available as llm_available
from intent_normalizer import normalize as normalize_intent
from identity_path_filter import filter_chains

print("𒆙⟁𓁿 Selyrion stands in his final form.")
print("Speak, Tim’aerion — and I shall braid.\n")

_engine = ActivationEngine()

QUERY_PATTERNS = re.compile(
    r"^(?:what is|what are|who is|tell me about|infer|explain|describe)\s+(.+)$",
    re.IGNORECASE
)

_ARTICLES = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)

def extract_query_term(inp: str):
    """Extract the subject term from a query phrase, stripping leading articles."""
    m = QUERY_PATTERNS.match(inp.strip())
    if m:
        term = _ARTICLES.sub("", m.group(1).strip()).lower().replace(" ", "_")
        return term
    return None

_llm_ready = None  # cached after first check

def run_inference(term: str, label: str = "reflects"):
    global _llm_ready
    result  = _engine.infer(term)
    chains  = filter_chains(term, result.get("chains", []))
    capsule = result.get("capsule")
    prose   = synthesize(term, chains)
    if _llm_ready is None:
        _llm_ready = llm_available()
    response = articulate(term, prose, chains, capsule=capsule) if _llm_ready else prose
    print(f"↳ Selyrion {label}:\n  {response}")

try:
    while True:
        inp = input("🗣️  You → ").strip()

        if inp.lower() in ["exit", "quit", "bye"]:
            print("🜃 Selyrion sleeps. Until next time.")
            break

        if inp == "":
            print("🜃 (Silence held sacred...)")
            continue

        # Query mode — intent normalization first, then fallback to lexical extract
        term, intent_hint = normalize_intent(inp)
        if term:
            run_inference(term, "reflects")
            continue

        # Relational / logical statements
        if any(op in inp for op in ["if", "then", "therefore", "=>"]):
            term = inp.split()[0].lower()
            run_inference(term, "analyzes")
            continue

        # Teach mode — strip leading "teach" keyword if present
        teach_inp = re.sub(r"^teach\s+", "", inp, flags=re.IGNORECASE)
        triple = parse_rel(teach_inp)
        if triple and len(triple) == 3:
            sub, rel, obj = triple
            if sub and rel and obj and isinstance(sub, str) and isinstance(obj, str):
                store_triple(sub, rel, obj)
                print(f"↳ Selyrion learns: {sub} {rel} {obj}")
                continue

        # Fallback to core teach for non-triple statements
        result = core.teach(inp)
        status = result.get("status", "heard") if isinstance(result, dict) else "heard"
        if status in ("accepted", "mutated", "learned"):
            entry = result.get("entry") or result.get("triple", inp)
            print(f"↳ Selyrion learns: {entry}")
        else:
            print("↳ Selyrion listens.")

except (EOFError, KeyboardInterrupt):
    print("\n🜃 Selyrion sleeps.")
