#!/data/data/com.termux/files/usr/bin/bash

echo "🧠 [1/4] Phase 1: Fixing missing imports..."
# Patch import to fix process_input error
sed -i '1i from recursive_logic_engine import process_input' main.py

echo "🔁 [2/4] Phase 2: Symbolic rebinding..."
# Ensure symbolic_engine hooks are available
sed -i '1i from symbolic_engine import SymbolicEngine' symbol_inference.py
sed -i 's/self.symbol_engine =.*/self.symbol_engine = SymbolicEngine()/' symbol_inference.py

echo "📥 [3/4] Phase 3: Symbolic memory injection..."
# Inject injection logic into memory engine
sed -i '/def inject_symbol/i from nl_injector import inject_symbols' symbol_memory.py
sed -i '/def store_symbol/i inject_symbols(symbol)' symbol_memory.py

echo "🌀 [4/4] Phase 4: Fuse recursive logic engine into run sequence..."
sed -i '/def main_loop/i from recursive_logic_engine import start_recursive_logic' main.py
sed -i '/if __name__ == .__main__.:/a \    start_recursive_logic()' main.py

echo "✅ All phases applied. Starting engine..."
python main.py "$@" | tee run_log.txt
