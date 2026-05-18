#!/bin/bash
sed -i 's/from symbolic_engine import SymbolicBrain/from symbolic_engine import SymbolicEngine as SymbolicBrain/' live_trainer.py
echo "[✓] Aliased SymbolicEngine as SymbolicBrain."
