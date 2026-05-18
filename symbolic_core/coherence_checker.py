# coherence_checker.py

from symbolic_core.coherence_net import CoherenceNet

# Default coherence threshold (adjustable)
COHERENCE_THRESHOLD = 0.3

def check_coherence(symbol: str) -> bool:
    """
    Check if a symbol passes the coherence threshold.

    Returns True if coherent, False if not.
    """
    net = CoherenceNet()
    score = net.evaluate(symbol)
    return score >= COHERENCE_THRESHOLD

def coherence_score(symbol: str) -> float:
    """
    Return the raw coherence score of the symbol.
    """
    net = CoherenceNet()
    return net.evaluate(symbol)

def evaluate_and_log(symbol: str) -> dict:
    """
    Evaluate coherence, flag contradiction, and return results with logging.
    """
    score = coherence_score(symbol)
    contradiction = score < COHERENCE_THRESHOLD

    result = {
        "symbol": symbol,
        "coherence_score": score,
        "contradiction": contradiction
    }

    # Optional: Add a logging mechanism if needed
    # (Reuses path logic from coherence_net.py if enabled)
    return result
