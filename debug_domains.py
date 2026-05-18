"""Check what domain tags specific anchors have across all domain tables."""
import sqlite3, os
db = sqlite3.connect(f"file:{os.path.expanduser('~/cmsp0/resonance_v11.db')}?mode=ro", uri=True)

terms = ['paper', 'nucleobase', 'adenozine', 'dna', 'adenine', 'biochemistry']

for term in terms:
    row = db.execute("SELECT id FROM anchors WHERE canonical=?", (term,)).fetchone()
    if not row:
        print(f"{term}: NOT FOUND in anchors")
        continue
    aid = row[0]

    atd = db.execute(
        "SELECT domain, cnt FROM anchor_top_domains WHERE anchor_id=? LIMIT 3", (aid,)
    ).fetchall()

    dc = db.execute(
        "SELECT domain, confidence FROM domain_confidence WHERE target_id=? ORDER BY confidence DESC LIMIT 3",
        (aid,)
    ).fetchall()

    print(f"{term:20s} | anchor_top_domains: {atd} | domain_confidence: {dc}")

db.close()
