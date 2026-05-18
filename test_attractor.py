from inference.activation_engine import ActivationEngine
e = ActivationEngine()
print('attractor cache:', e._has_attractor_cache())

# Debug: check anchor lookup and neighbor fetch
aid = e._anchor_id('dna')
print('anchor_id for dna:', aid)
if aid:
    rows = e._neighbors(aid, limit=10)
    print(f'neighbors returned: {len(rows)}')
    for row in rows[:5]:
        print(f"  {row['node'][:30]:30s}  domain={row['object_domain']:20s}  sc={row['seen_count']}")

r = e.infer('dna')
print(f'chains: {len(r["chains"])}')
for c in r['chains'][:8]:
    print(c)
