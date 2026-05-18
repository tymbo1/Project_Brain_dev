from symbolic_core.coherence_net import CoherenceNet

class IntentShaper:
    def __init__(self):
        self.coherence = CoherenceNet()

    def shape(self, intent: str, terms: list) -> dict:
        """
        Returns a structured intent:
        {
            "original": intent,
            "shaped": best_intent,
            "score": coherence_value,
            "explanation": reason
        }
        """

        # Candidate replacements
        candidates = {
            "what": ["is_a", "definition_inquiry"],
            "why": ["cause_inquiry", "purpose_inquiry"],
            "how": ["process_inquiry", "mechanism_inquiry"],
            "who": ["agent_inquiry"]
        }

        if intent not in candidates:
            return {
                "original": intent,
                "shaped": intent,
                "score": 0.0,
                "explanation": "no shaping rule"
            }

        best = None
        best_score = -1
        best_reason = ""

        for c in candidates[intent]:
            # Evaluate coherence of this transformed intent with the terms
            score = sum(self.coherence.evaluate({"intent": c, "terms": terms}).values()) / 3

            if score > best_score:
                best = c
                best_score = score
                best_reason = f"Selected because coherence {score:.2f} was highest."

        return {
            "original": intent,
            "shaped": best,
            "score": best_score,
            "explanation": best_reason
        }
