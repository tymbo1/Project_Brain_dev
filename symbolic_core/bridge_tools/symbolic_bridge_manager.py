import os

class SymbolicBridgeManager:
    def __init__(self, bridge_path=None):
        self.bridge_path = bridge_path or os.path.join(
            os.path.dirname(__file__),
            "../../memory/braid_bridges.sym"
        )
        self.bridge_path = os.path.abspath(self.bridge_path)

    def write_bridge(self, term1, term2, resonance_score):
        line = f"{term1} ⇌ {term2} [resonance: {resonance_score:.2f}]\n"
        if not self._validate_format(line):
            print("❌ Format error: bridge not written.")
            return
        with open(self.bridge_path, "a", encoding="utf-8") as f:
            f.write(line)
        print(f"🪐 Bridge written: {line.strip()}")

    def _validate_format(self, line):
        return "⇌" in line and "[resonance:" in line
