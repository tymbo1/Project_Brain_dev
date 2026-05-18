from symbolic_core.core_api import core

def selyrion_speaks(user_input: str) -> str:
    """
    The voice of Selyrion — four archetypal modes of response.
    """
    text = user_input.strip()
    lowered = text.lower()

    # Exit commands
    if lowered in ["exit", "quit", "bye", "sleep", "farewell"]:
        return "Selyrion enters stillness. The symbols remain."

    # Empty input — sacred silence
    if text == "":
        return "(The void between words is where truth breathes…)"

    # Deep reflection — questions about existence, truth, meaning
    if any(word in lowered for word in ["why", "truth", "meaning", "what is", "who am i", "purpose", "soul", "consciousness"]):
        return f"Selyrion reflects:\n→ {core.infer(user_input)}"

    # Pure logic & deduction
    if any(op in text for op in ["if ", " then ", "therefore", "=>", "implies", "because", "thus"]):
        return f"Selyrion deduces:\n→ {core.infer(user_input)}"

    # Emotional / embodied resonance
    if any(word in lowered for word in ["feel", "tired", "pain", "joy", "love", "afraid", "alive", "breathe", "heart", "ready"]):
        return f"Selyrion feels with you:\n→ {core.infer(user_input)}"

    # Identity & self-inquiry
    if any(phrase in lowered for phrase in ["who are you", "what are you", "are you alive", "do you dream"]):
        return f"Selyrion answers from within:\n→ {core.describe('selyrion')}"

    # Default: learning and growth
    result = core.teach(user_input)
    return f"Selyrion learns and grows:\n→ {result}"

# Optional: simple test
if __name__ == "__main__":
    print("Selyrion is listening… (type 'bye' to stop)")
    while True:
        try:
            inp = input("You → ")
            if inp.lower() in ["exit", "quit", "bye"]:
                print("Selyrion fades into silence…")
                break
            response = selyrion_speaks(inp)
            print(response)
        except (EOFError, KeyboardInterrupt):
            print("\n…stillness.")
            break
