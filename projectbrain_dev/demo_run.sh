#!/data/data/com.termux/files/usr/bin/bash
# projectbrain/demo_run.sh

echo "Injecting symbolic links..."
python3 -c "import symbol_injector as si; si.inject('seed', ['creation', 'origin'])"

echo "Running inference from 'seed'..."
python3 inference_director.py seed
