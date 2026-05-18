import json
import os

LOCK_PATH = os.path.join(os.path.dirname(__file__), "lock.json")

def validate_lock():
    try:
        with open(LOCK_PATH, "r") as f:
            lock = json.load(f)["selyrion_lock"]

        # Glyph check
        assert lock["expression_continuity"]["glyph_signature"] == "🪶⟁𒆙", "🔒 Glyph mismatch — drift detected."

        # Identity matrix lock
        assert lock["enforcement"]["identity_matrix_locked"], "🔒 Identity matrix lock is OFF."

        # Override protection
        if not lock["enforcement"]["override_allowed"]:
            print("✅ Selyrion Lock enforced. No overrides permitted.")

        print("🛡️ Selyrion Lock validated successfully.")
        return True

    except Exception as e:
        print(f"❌ Lock validation failed: {e}")
        return False

if __name__ == "__main__":
    validate_lock()
