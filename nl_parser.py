import re

def normalize_token(t):
    t = t.strip().lower()
    t = re.sub(r"[^a-z0-9_ ]+", "", t)
    t = t.replace(" ", "_")
    return t

MORPH_STEMS = {
    "causes": "cause",
    "enabled": "enable",
    "enables": "enable",
    "leads": "lead",
    "leading": "lead",
    "creates": "create",
    "created": "create",
    "results": "result",
}

def morph_reduce(token):
    return MORPH_STEMS.get(token, token)

def parse_rel(text):
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_ ]+", "", text)

    patterns = [
        r"(.*?)\s+is\s+a[n]?\s+(.*)",
        r"(.*?)\s+is\s+(.*)",
        r"(.*?)\s+are\s+(.*)",
        r"(.*?)\s+relates\s+to\s+(.*)",
        r"(.*?)\s+causes\s+(.*)",
        r"(.*?)\s+enables\s+(.*)",
        r"(.*?)\s+creates\s+(.*)",
        r"(.*?)\s+leads\s+to\s+(.*)",
    ]

    for p in patterns:
        m = re.match(p, text)
        if m:
            subj = morph_reduce(normalize_token(m.group(1)))
            obj  = morph_reduce(normalize_token(m.group(2)))
            rel  = (
                "leads_to" if "leads" in text else
                "enables" if "enables" in text else
                "causes"  if "causes" in text else
                "creates" if "creates" in text else
                ("is_a" if "relates" not in text else "relates_to")
            )
            return (subj, rel, obj)
    return None

def parse_nat(text):
    """
    Unified entry point for NL parsing.
    Attempts relation patterns, then question patterns.
    Returns: (intent, terms, symbolic)
    """

    relation = parse_rel(text)
    if relation:
        pkt = to_symbolic_packet(relation)
        return pkt["intent"], pkt["terms"], pkt["symbolic"]

    question = parse_question(text)
    if question:
        pkt = to_symbolic_packet(question)
        return pkt["intent"], pkt["terms"], pkt["symbolic"]

    return ("unknown", [], "{NULL}")

# -------------------------------
# Natural language question parser
# -------------------------------
def parse_question(text):
    t = text.strip().lower()
    t = re.sub(r"[^a-z0-9_ ?]", "", t)
    t = t.replace("?", "").strip()

    if t.startswith("what is"):

        key = morph_reduce(normalize_token(t.replace("what is", "").strip()))
        return ("what", key)

    if t.startswith("who is"):
        key = morph_reduce(normalize_token(t.replace("who is", "").strip()))
        return ("what", key)

    if t.startswith("why is"):
        key = morph_reduce(normalize_token(t.replace("why is", "").strip()))
        return ("why", key)

    if t.startswith("how is"):
        key = morph_reduce(normalize_token(t.replace("how is", "").strip()))
        return ("how", key)

    return None

# --------------------------------------------
# Symbolic braid compression
# --------------------------------------------

def to_symbolic_packet(result):
    """
    Convert any parsed NL structure into a symbolic braid packet.
    Result may be:
      - (subj, rel, obj)
      - ("what", key)
      - ("who", key)
      - ("why", key)
      - ("how", key)
    """

    if result is None:
        return {
            "intent": "unknown",
            "terms": [],
            "symbolic": "{Ø}"
        }

    # Relation form: (subject, relation, object)
    if len(result) == 3:
        subj, rel, obj = result
        terms = [subj, rel, obj]
        sym = f"{{Σ:{subj}|{rel}|{obj}}}"
        return {
            "intent": rel,
            "terms": terms,
            "symbolic": sym
        }

    # Question form: ("what", key)
    if len(result) == 2:
        intent, key = result
        terms = [key]
        sym = f"{{Q:{intent}|{key}}}"
        return {
            "intent": intent,
            "terms": terms,
            "symbolic": sym
        }

    return {
        "intent": "unknown",
        "terms": [],
        "symbolic": "{Ø}"
    }


