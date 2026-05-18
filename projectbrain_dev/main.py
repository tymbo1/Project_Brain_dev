#!/usr/bin/env python3
import sys, os, re
sys.path.append(os.path.join(os.path.dirname(__file__), "symbolic_core"))
from symbolic_core.core_api import core
from inference.activation_engine import ActivationEngine
from memory.memory_core import store_triple
from nl_parser import parse_nat, parse_rel
from nl_synthesis import synthesize

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

def run_inference(term: str, label: str = "reflects"):
    result = _engine.infer(term)
    chains = result.get("chains", [])
    response = synthesize(term, chains)
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

        # Query mode — extract term and run inference
        term = extract_query_term(inp)
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
