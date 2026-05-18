#!/data/data/com.termux/files/usr/bin/python
# Save as: symbolic_cli.py
import sys
from symbolic_core import core

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "teach":
        phrase = " ".join(sys.argv[2:])
        print("Taught:", core.teach(phrase))
    elif cmd == "query":
        term = sys.argv[2]
        print("Query:", core.query(term))
    elif cmd == "dump":
        from pprint import pprint
        pprint(core.dump_memory())
    else:
        print("Usage: symbolic_cli.py teach <phrase> | query <word> | dump")
