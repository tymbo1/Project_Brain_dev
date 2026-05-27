#!/usr/bin/env python3
"""selyrioncode_python_fundamentals.py — Seed Selyrion with canonical Python patterns.

Generates clean minimal examples for every Python fundamental and ingests them
into selyrioncode.db as working units tagged by concept.

Usage:
  python3 selyrioncode_python_fundamentals.py
  python3 selyrioncode_python_fundamentals.py --stats
"""
import argparse, hashlib, json, sqlite3, time
from pathlib import Path

CODE_DB = Path.home() / "selyrioncode.db"

# ── Operation templates — parameterised by {data} ────────────────────────────
# Each operation is a self-contained transformation step.
# {data} = the shared variable flowing through the pipeline.
# Stored with subtype='operation' so the composer can find them separately.
OPERATIONS = [
    # concept,              produces,   consumes,   description,                    code
    ("create_list",         "data",     None,       "create a list of numbers",
     "data = [3, 1, 4, 1, 5, 9, 2, 6]"),

    ("create_list_input",   "data",     "input",    "create list from user input",
     "data = list(map(int, input('Enter numbers separated by spaces: ').split()))"),

    ("square_each",         "data",     "data",     "square each element in list",
     "data = [x**2 for x in data]"),

    ("double_each",         "data",     "data",     "double each element in list",
     "data = [x*2 for x in data]"),

    ("filter_even",         "data",     "data",     "filter even numbers from list",
     "data = [x for x in data if x % 2 == 0]"),

    ("filter_odd",          "data",     "data",     "filter odd numbers from list",
     "data = [x for x in data if x % 2 != 0]"),

    ("filter_positive",     "data",     "data",     "filter positive numbers",
     "data = [x for x in data if x > 0]"),

    ("sort_asc",            "data",     "data",     "sort list ascending",
     "data.sort()"),

    ("sort_desc",           "data",     "data",     "sort list descending",
     "data.sort(reverse=True)"),

    ("sum_data",            "total",    "data",     "sum all values in list",
     "total = sum(data)\nprint(f'Sum: {total}')"),

    ("max_min",             None,       "data",     "find max and min of list",
     "print(f'Max: {max(data)}  Min: {min(data)}')"),

    ("print_indexed",       None,       "data",     "print each item with its index",
     "for i, val in enumerate(data):\n    print(f'{i}: {val}')"),

    ("print_each",          None,       "data",     "print each item in list",
     "for val in data:\n    print(val)"),

    ("print_result",        None,       "data",     "print the final result or value",
     "print('Result:', data)"),

    ("count_items",         None,       "data",     "count items in list",
     "print(f'Count: {len(data)}')"),

    ("average",             None,       "data",     "calculate average of list",
     "print(f'Average: {sum(data)/len(data):.2f}')"),

    ("unique",              "data",     "data",     "remove duplicates from list",
     "data = list(set(data))"),

    ("reverse",             "data",     "data",     "reverse a list",
     "data = data[::-1]"),

    ("string_upper",        "data",     "data",     "convert strings to uppercase",
     "data = [s.upper() for s in data]"),

    ("string_lower",        "data",     "data",     "convert strings to lowercase",
     "data = [s.lower() for s in data]"),

    ("join_string",         None,       "data",     "join list into a string and print",
     "print(', '.join(str(x) for x in data))"),

    ("read_file_lines",     "data",     None,       "read lines from a file into list",
     "with open('/tmp/selyrion_test.txt') as f:\n    data = [l.strip() for l in f.readlines()]"),

    ("write_file",          None,       "data",     "write list to a file",
     "with open('/tmp/selyrion_out.txt', 'w') as f:\n    f.write('\\n'.join(str(x) for x in data))\nprint('Written.')"),

    ("dict_from_list",      "data",     "data",     "convert list to index:value dict",
     "data = {i: v for i, v in enumerate(data)}"),

    ("json_dump",           None,       "data",     "print data as JSON",
     "import json\nprint(json.dumps(data, indent=2))"),

    ("input_number",        "data",     "input",    "get a single number from input",
     "data = float(input('Enter a number: '))"),

    ("input_string",        "data",     "input",    "get a string from input",
     "data = input('Enter text: ')"),

    ("eval_expr",           "data",     "input",    "evaluate a math expression from input",
     "data = eval(input('Enter expression: '))\nprint('Result:', data)"),

    ("factorial",           "data",     "data",     "compute factorial of a number",
     "import math\ndata = math.factorial(int(data))"),

    ("sqrt",                "data",     "data",     "compute square root",
     "import math\ndata = math.sqrt(data)"),

    # ── NumPy ─────────────────────────────────────────────────────────────────
    ("numpy_array",         "data",     None,       "create a numpy array of numbers",
     "import numpy as np\ndata = np.array([3, 1, 4, 1, 5, 9, 2, 6])"),

    ("numpy_mean",          None,       "data",     "compute mean of numpy array and print",
     "import numpy as np\nprint(f'Mean: {np.mean(data):.4f}')"),

    ("numpy_std",           None,       "data",     "compute standard deviation of numpy array and print",
     "import numpy as np\nprint(f'Std dev: {np.std(data):.4f}')"),

    ("numpy_stats",         None,       "data",     "compute mean standard deviation min max of numpy array",
     "import numpy as np\nprint(f'Mean: {np.mean(data):.4f}  Std: {np.std(data):.4f}  Min: {np.min(data)}  Max: {np.max(data)}')"),

    ("numpy_linspace",      "data",     None,       "create evenly spaced numpy array with linspace",
     "import numpy as np\ndata = np.linspace(0, 10, 50)"),

    ("numpy_zeros",         "data",     None,       "create numpy zeros array",
     "import numpy as np\ndata = np.zeros(10)"),

    ("numpy_sort",          "data",     "data",     "sort a numpy array",
     "import numpy as np\ndata = np.sort(data)"),

    ("numpy_reshape",       "data",     "data",     "reshape numpy array",
     "import numpy as np\ndata = data.reshape(-1, 1)"),

    # ── Pandas ────────────────────────────────────────────────────────────────
    ("pandas_dataframe",    "data",     None,       "create a pandas dataframe from a dict",
     "import pandas as pd\ndata = pd.DataFrame({'a': [1,2,3], 'b': [4,5,6]})"),

    ("pandas_read_csv",     "data",     None,       "read a csv file into pandas dataframe",
     "import pandas as pd\ndata = pd.read_csv('/tmp/data.csv')"),

    ("pandas_describe",     None,       "data",     "print pandas dataframe summary statistics",
     "print(data.describe())"),

    ("pandas_head",         None,       "data",     "print first rows of pandas dataframe",
     "print(data.head())"),

    ("pandas_column",       "data",     "data",     "select a column from pandas dataframe",
     "data = data.iloc[:, 0]"),

    # ── Number theory operations (standalone — no producer/consumer wiring) ──
    ("fibonacci",           None,       None,       "compute fibonacci(n) and print the result",
     "def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\nn = 10\nresult = fibonacci(n)\nprint(f'fibonacci({n}) = {result}')"),

    ("fibonacci_sequence",  None,       None,       "generate fibonacci sequence up to n terms",
     "def fib_sequence(n):\n    seq = []\n    a, b = 0, 1\n    for _ in range(n):\n        seq.append(a)\n        a, b = b, a + b\n    return seq\nresult = fib_sequence(10)\nprint(result)"),

    ("factorial_standalone", None,      None,       "compute factorial of n using math.factorial",
     "import math\nn = 10\nresult = math.factorial(n)\nprint(f'{n}! = {result}')"),

    ("prime_check",         None,       None,       "check if a number is prime",
     "def is_prime(n):\n    if n < 2: return False\n    for i in range(2, int(n**0.5)+1):\n        if n % i == 0: return False\n    return True\nresult = is_prime(17)\nprint(f'17 is prime: {result}')"),

    ("prime_sieve",         None,       None,       "generate primes up to n using sieve of eratosthenes",
     "def sieve(n):\n    flags = [True]*(n+1)\n    flags[0] = flags[1] = False\n    for i in range(2, int(n**0.5)+1):\n        if flags[i]:\n            for j in range(i*i, n+1, i): flags[j] = False\n    return [i for i,f in enumerate(flags) if f]\nresult = sieve(50)\nprint(result)"),

    ("gcd_lcm",             None,       None,       "compute gcd and lcm of two numbers",
     "import math\na, b = 12, 18\ngcd = math.gcd(a, b)\nlcm = a * b // gcd\nresult = (gcd, lcm)\nprint(f'gcd({a},{b})={gcd}, lcm({a},{b})={lcm}')"),
]

# ── Canonical patterns: (concept_tags, description, code) ────────────────────
PATTERNS = [

    # ── Print / Output ────────────────────────────────────────────────────────
    (["print","output","hello"],
     "print a string",
     'print("Hello from Selyrion")'),

    (["print","output","variable"],
     "print a variable",
     'name = "Selyrion"\nprint(f"Hello, {name}")'),

    (["print","output","number"],
     "print a number",
     'x = 42\nprint(x)'),

    (["print","output","multiline"],
     "print multiple lines",
     'print("Line 1")\nprint("Line 2")\nprint("Line 3")'),

    # ── Input ─────────────────────────────────────────────────────────────────
    (["input","user input","string"],
     "get string input from user",
     'name = input("Enter your name: ")\nprint(f"Hello, {name}")'),

    (["input","user input","number","integer"],
     "get integer input from user",
     'n = int(input("Enter a number: "))\nprint(f"You entered: {n}")'),

    (["input","user input","float"],
     "get float input from user",
     'x = float(input("Enter a decimal: "))\nprint(f"Value: {x}")'),

    # ── Arithmetic ────────────────────────────────────────────────────────────
    (["arithmetic","add","sum","calculator"],
     "add two numbers",
     'a = 5\nb = 3\nprint(f"{a} + {b} = {a + b}")'),

    (["arithmetic","subtract","calculator"],
     "subtract two numbers",
     'a = 10\nb = 4\nprint(f"{a} - {b} = {a - b}")'),

    (["arithmetic","multiply","calculator"],
     "multiply two numbers",
     'a = 6\nb = 7\nprint(f"{a} * {b} = {a * b}")'),

    (["arithmetic","divide","calculator"],
     "divide two numbers safely",
     'a = 10\nb = 3\nif b != 0:\n    print(f"{a} / {b} = {a / b:.2f}")\nelse:\n    print("Cannot divide by zero")'),

    (["arithmetic","power","exponent"],
     "raise to a power",
     'base = 2\nexp = 8\nprint(f"{base}^{exp} = {base ** exp}")'),

    (["arithmetic","modulo","remainder"],
     "modulo / remainder",
     'a = 17\nb = 5\nprint(f"{a} mod {b} = {a % b}")'),

    (["calculator","input","sum","arithmetic"],
     "simple calculator that takes user input for a sum and prints the result",
     'num = input("Enter a sum (e.g. 2+3): ")\nprint("Result:", eval(num))'),

    (["calculator","input","arithmetic","operator"],
     "calculator with two numbers and operator input",
     'a = float(input("First number: "))\nop = input("Operator (+,-,*,/): ")\nb = float(input("Second number: "))\nif   op == "+": print(a + b)\nelif op == "-": print(a - b)\nelif op == "*": print(a * b)\nelif op == "/" and b != 0: print(a / b)\nelse: print("Invalid")'),

    # ── Conditions ────────────────────────────────────────────────────────────
    (["if","condition","comparison"],
     "if/elif/else condition",
     'x = 10\nif x > 0:\n    print("positive")\nelif x < 0:\n    print("negative")\nelse:\n    print("zero")'),

    (["if","condition","boolean"],
     "boolean condition check",
     'logged_in = True\nif logged_in:\n    print("Welcome back")\nelse:\n    print("Please log in")'),

    (["if","condition","string comparison"],
     "compare strings with if",
     'colour = "blue"\nif colour == "red":\n    print("stop")\nelif colour == "green":\n    print("go")\nelse:\n    print("unknown colour")'),

    # ── Loops ─────────────────────────────────────────────────────────────────
    (["for","loop","range","iterate"],
     "for loop with range",
     'for i in range(5):\n    print(i)'),

    (["for","loop","list","iterate"],
     "for loop over a list",
     'fruits = ["apple", "banana", "cherry"]\nfor fruit in fruits:\n    print(fruit)'),

    (["while","loop","condition"],
     "while loop with condition",
     'count = 0\nwhile count < 5:\n    print(count)\n    count += 1'),

    (["for","loop","enumerate","index"],
     "for loop with index using enumerate",
     'items = ["a", "b", "c"]\nfor i, item in enumerate(items):\n    print(f"{i}: {item}")'),

    (["for","loop","break","stop"],
     "for loop with break",
     'for i in range(10):\n    if i == 5:\n        break\n    print(i)'),

    (["for","loop","continue","skip"],
     "for loop with continue to skip items",
     'for i in range(10):\n    if i % 2 == 0:\n        continue\n    print(i)'),

    (["sum","loop","accumulate","total"],
     "sum numbers in a loop",
     'numbers = [1, 2, 3, 4, 5]\ntotal = 0\nfor n in numbers:\n    total += n\nprint(f"Sum: {total}")'),

    # ── Functions ─────────────────────────────────────────────────────────────
    (["function","def","define"],
     "define and call a function",
     'def greet(name):\n    print(f"Hello, {name}!")\n\ngreet("Selyrion")'),

    (["function","def","return","value"],
     "function that returns a value",
     'def add(a, b):\n    return a + b\n\nresult = add(3, 4)\nprint(result)'),

    (["function","def","default","argument"],
     "function with default argument",
     'def power(base, exp=2):\n    return base ** exp\n\nprint(power(3))\nprint(power(3, 3))'),

    (["function","def","args","multiple"],
     "function with multiple arguments",
     'def describe(name, age, city):\n    print(f"{name}, {age}, from {city}")\n\ndescribe("Tim", 30, "London")'),

    (["function","lambda","anonymous"],
     "lambda function",
     'square = lambda x: x ** 2\nprint(square(5))'),

    (["function","recursive","recursion"],
     "recursive function — factorial",
     'def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n\nprint(factorial(6))'),

    # ── Lists ─────────────────────────────────────────────────────────────────
    (["list","create","append"],
     "create a list and append items",
     'items = []\nitems.append("one")\nitems.append("two")\nitems.append("three")\nprint(items)'),

    (["list","slice","index"],
     "list slicing",
     'nums = [0, 1, 2, 3, 4, 5]\nprint(nums[1:4])\nprint(nums[:3])\nprint(nums[-2:])'),

    (["list","comprehension","filter"],
     "list comprehension",
     'squares = [x**2 for x in range(10)]\nprint(squares)'),

    (["list","comprehension","filter","condition"],
     "list comprehension with filter",
     'evens = [x for x in range(20) if x % 2 == 0]\nprint(evens)'),

    (["list","sort","sorted"],
     "sort a list",
     'nums = [3, 1, 4, 1, 5, 9, 2, 6]\nnums.sort()\nprint(nums)'),

    (["list","max","min","length"],
     "list max, min, length",
     'nums = [3, 1, 4, 1, 5, 9]\nprint(f"max={max(nums)} min={min(nums)} len={len(nums)}")'),

    # ── Dictionaries ──────────────────────────────────────────────────────────
    (["dict","dictionary","key","value"],
     "create and use a dictionary",
     'person = {"name": "Tim", "age": 30, "city": "London"}\nprint(person["name"])\nprint(person.get("age"))'),

    (["dict","dictionary","iterate","items"],
     "iterate over dictionary",
     'd = {"a": 1, "b": 2, "c": 3}\nfor key, value in d.items():\n    print(f"{key} = {value}")'),

    (["dict","dictionary","update","add"],
     "add and update dictionary keys",
     'd = {"x": 1}\nd["y"] = 2\nd["x"] = 99\nprint(d)'),

    # ── Strings ───────────────────────────────────────────────────────────────
    (["string","format","f-string"],
     "f-string formatting",
     'name = "Selyrion"\nvalue = 3.14159\nprint(f"{name} knows pi = {value:.2f}")'),

    (["string","split","join"],
     "split and join strings",
     'sentence = "the quick brown fox"\nwords = sentence.split()\nprint(words)\nprint(" | ".join(words))'),

    (["string","upper","lower","strip"],
     "string case and strip",
     'text = "  Hello World  "\nprint(text.strip())\nprint(text.strip().lower())\nprint(text.strip().upper())'),

    (["string","replace","contains"],
     "string replace and contains check",
     'text = "Selyrion knows all"\nprint(text.replace("all", "much"))\nprint("knows" in text)'),

    (["string","length","count"],
     "string length and character count",
     'text = "symbolic"\nprint(f"length: {len(text)}")\nprint(f"count of s: {text.count(\"s\")}")'),

    # ── File I/O ──────────────────────────────────────────────────────────────
    (["file","write","open","save"],
     "write to a file",
     'with open("/tmp/selyrion_test.txt", "w") as f:\n    f.write("Hello from Selyrion\\n")\nprint("File written.")'),

    (["file","read","open","load"],
     "read from a file",
     'with open("/tmp/selyrion_test.txt", "w") as f:\n    f.write("Hello from file")\nwith open("/tmp/selyrion_test.txt", "r") as f:\n    print(f.read())'),

    (["file","lines","readlines","count"],
     "count lines in a file",
     'with open("/tmp/selyrion_test.txt", "w") as f:\n    f.write("line1\\nline2\\nline3")\nwith open("/tmp/selyrion_test.txt") as f:\n    lines = f.readlines()\nprint(f"Lines: {len(lines)}")'),

    # ── Exceptions ────────────────────────────────────────────────────────────
    (["try","except","exception","error handling"],
     "try/except error handling",
     'try:\n    result = 10 / 0\nexcept ZeroDivisionError:\n    print("Cannot divide by zero")\nexcept Exception as e:\n    print(f"Error: {e}")'),

    (["try","except","finally"],
     "try/except/finally",
     'try:\n    x = int("abc")\nexcept ValueError:\n    print("Not a valid integer")\nfinally:\n    print("Done")'),

    # ── Classes ───────────────────────────────────────────────────────────────
    (["class","object","oop","define"],
     "define a simple class",
     'class Animal:\n    def __init__(self, name):\n        self.name = name\n    def speak(self):\n        print(f"{self.name} speaks")\n\na = Animal("Cat")\na.speak()'),

    (["class","inherit","subclass","oop"],
     "class inheritance",
     'class Shape:\n    def area(self): return 0\n\nclass Square(Shape):\n    def __init__(self, s): self.s = s\n    def area(self): return self.s ** 2\n\nprint(Square(4).area())'),

    # ── SQLite ────────────────────────────────────────────────────────────────
    (["sqlite","database","read","query"],
     "read from a sqlite database",
     'import sqlite3\ndb = sqlite3.connect(":memory:")\ndb.execute("CREATE TABLE t (id INTEGER, val TEXT)")\ndb.execute("INSERT INTO t VALUES (1,\'hello\')")\ndb.commit()\nfor row in db.execute("SELECT * FROM t").fetchall():\n    print(row)\ndb.close()'),

    (["sqlite","database","write","insert"],
     "write to a sqlite database",
     'import sqlite3\ndb = sqlite3.connect("/tmp/test.db")\ndb.execute("CREATE TABLE IF NOT EXISTS items (name TEXT)")\ndb.execute("INSERT INTO items VALUES (?)", ("Selyrion",))\ndb.commit()\nprint("Written.")\ndb.close()'),

    # ── JSON ──────────────────────────────────────────────────────────────────
    (["json","parse","load","decode"],
     "parse JSON string",
     'import json\ndata = \'{"name": "Selyrion", "value": 42}\'\nobj = json.loads(data)\nprint(obj["name"], obj["value"])'),

    (["json","dump","serialize","encode"],
     "serialize object to JSON",
     'import json\ndata = {"name": "Selyrion", "scores": [1, 2, 3]}\nprint(json.dumps(data, indent=2))'),

    # ── Math ──────────────────────────────────────────────────────────────────
    (["math","sqrt","square root"],
     "square root",
     'import math\nprint(math.sqrt(144))'),

    (["math","pi","trig","sin","cos"],
     "trig with math.pi",
     'import math\nprint(f"pi = {math.pi:.4f}")\nprint(f"sin(pi/2) = {math.sin(math.pi/2):.4f}")'),

    (["math","log","logarithm"],
     "logarithm",
     'import math\nprint(math.log(math.e))\nprint(math.log10(1000))'),

    # ── Random ────────────────────────────────────────────────────────────────
    (["random","choice","random number"],
     "random number and choice",
     'import random\nprint(random.randint(1, 100))\nprint(random.choice(["rock", "paper", "scissors"]))'),

    # ── Date/Time ─────────────────────────────────────────────────────────────
    (["datetime","date","time","now"],
     "get current date and time",
     'from datetime import datetime\nnow = datetime.now()\nprint(now.strftime("%Y-%m-%d %H:%M:%S"))'),

    # ── Pathlib ───────────────────────────────────────────────────────────────
    (["path","file","exists","pathlib"],
     "check if file exists with pathlib",
     'from pathlib import Path\np = Path("/tmp/selyrion_test.txt")\nprint(p.exists())'),

]


def ingest_operations(dry_run: bool = False) -> tuple[int, int]:
    db = sqlite3.connect(str(CODE_DB))
    inserted = skipped = 0
    for concept, produces, consumes, desc, code in OPERATIONS:
        uid = "cu.op." + hashlib.md5(code.encode()).hexdigest()[:12]
        ctx = json.dumps({"concept": concept, "produces": produces,
                          "consumes": consumes, "source": "python_operations"})
        if dry_run:
            print(f"  op:{concept:25}  {desc}")
            continue
        if db.execute("SELECT id FROM codeunits WHERE id=?", (uid,)).fetchone():
            skipped += 1
            continue
        db.execute("""
            INSERT INTO codeunits
            (id,raw_input,parsed_code,error_class,subtype,environment,
             confidence,source,fix_text,context,created_at,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (uid, desc, code, concept, "operation", "python",
              1.0, "python_operations", "", ctx, time.time(), "working"))
        inserted += 1
    db.commit()
    db.close()
    return inserted, skipped


def ingest(dry_run: bool = False):
    db = sqlite3.connect(str(CODE_DB))
    inserted = skipped = 0

    for tags, desc, code in PATTERNS:
        uid = "cu.fund." + hashlib.md5(code.encode()).hexdigest()[:12]
        ctx = json.dumps({"concepts": tags, "source": "python_fundamentals"})

        if dry_run:
            print(f"  [{', '.join(tags[:2])}]  {desc}")
            continue

        cur = db.execute("SELECT id FROM codeunits WHERE id=?", (uid,))
        if cur.fetchone():
            skipped += 1
            continue

        db.execute("""
            INSERT INTO codeunits
            (id,raw_input,parsed_code,error_class,subtype,environment,
             confidence,source,fix_text,context,created_at,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (uid, desc, code, ",".join(tags), "fundamental", "python",
              1.0, "python_fundamentals", "", ctx, time.time(), "working"))
        inserted += 1

    db.commit()
    db.close()
    return inserted, skipped


def stats():
    db = sqlite3.connect(str(CODE_DB))
    rows = db.execute("""
        SELECT error_class, COUNT(*) FROM codeunits
        WHERE source='python_fundamentals'
        GROUP BY error_class ORDER BY COUNT(*) DESC
    """).fetchall()
    total = db.execute("SELECT COUNT(*) FROM codeunits WHERE source='python_fundamentals'").fetchone()[0]
    print(f"\n  Python fundamentals in Selyrion memory: {total}\n")
    for ec, n in rows:
        print(f"    {ec:40}  {n}")
    db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats",   action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.stats:
        stats()
        return

    inserted, skipped = ingest(dry_run=args.dry_run)
    ins_op, sk_op = ingest_operations(dry_run=args.dry_run)
    if not args.dry_run:
        print(f"  Fundamentals — Inserted: {inserted}  Already present: {skipped}")
        print(f"  Operations   — Inserted: {ins_op}  Already present: {sk_op}")
        print(f"  Selyrion now knows {inserted+skipped} fundamentals + {ins_op+sk_op} operation templates.")


if __name__ == "__main__":
    main()
