# Linguistics Pass 3 — GPT HITL Review

Dry-run output: 364 proposed relations, 55 concepts.
18 concepts returned 0 relations (LLM JSON parse errors — need rerun or manual skip).

---

## LLM errors (0 output) — concepts to skip or rerun

fricative, affricate, voiced, alveolar, rime, mora, glottal, complement, head,
adverb, determiner, synonym, antonym, prototype, denotation, compositionality,
scope

Note: "fricative" had 11 raw but 0 valid (all filtered — likely already in DB from Pass 2).

---

## Flagged relations — review required

### 🔴 WRONG — reject

| # | Relation | Reason |
|---|----------|--------|
| 1 | `bilabial --is_a--> manner of articulation` | bilabial = PLACE of articulation, not manner |
| 2 | `phrase --contains--> sentence` (from coda block) | reversed — sentence contains phrase |
| 3 | `root --contains--> stem` (from root block) | reversed — stem contains root as its base |
| 4 | `stem --part_of--> root` (from root block) | reversed — root is part of stem |
| 5 | `articulatory features --part_of--> velar` | reversed — velar is part of articulatory features |
| 6 | `predicate logic --contains--> propositional logic` | reversed — propositional logic is simpler/subset, not contained by PL |
| 7 | `semivowel --is_a--> vowel` | wrong — semivowels (w, j) are not vowels; they are approximants |
| 8 | `noun --contains--> article` | articles are determiners not part of noun (part of NP) |
| 9 | `noun --requires--> nominalization` | reversed — nominalization creates nouns |
| 10 | `verb --contains--> phrase` | confuses verb with verb phrase |
| 11 | `verb --contains--> phrase structure` | same confusion |
| 12 | `grammar --enables--> lexeme` | odd direction; lexemes are units of grammar, not enabled by it |

### 🟡 OFF-TOPIC — reject (drifted from seed concept)

From [subject] block — LLM wandered away from grammatical subject:
- `sentence --part_of--> text [0.91]`
- `verb --enables--> tense [0.97]`
- `tense --derived_from--> aspect [0.99]`
- `action --related_to--> event [0.98]`
- `event --contains--> state [0.95]`
- `state --distinct_from--> process [0.93]`
- `noun --related_to--> category [0.92]`

From [object] block — same drift:
- `sentence --part_of--> text [0.88]`
- `text --contains--> sentence [0.85]`

From [root] block:
- `linguistic unit --is_a--> language [0.85]` (off-topic for root)

From [stem] block:
- `word form --is_a--> form [0.98]` (too generic)

From [adjective] block:
- `adjective --contains--> descriptive information [0.9]` ("descriptive information" is not a linguistics concept)

### 🟡 LOW-CONFIDENCE — reject (≤ 0.35)

- `pronoun --distinct_from--> quantifier [0.35]`

---

## Specific approve notes

### 🟢 CORRECT despite potential flag

- `stem --contains--> root` — **KEEP** (stem is root + affixes, so stem does contain root)
- `root --part_of--> word` — keep
- `foot --is_a--> metrical unit` + `foot --contains--> syllable` — keep (metrical foot)
- `foot --part_of--> verse` — borderline; keep if "verse" resolves as anchor
- `polysemy --part_of--> semantics` — keep
- `predicate logic --part_of--> mathematical logic` — keep (correct)
- `predicate logic --is_a--> formal logic` — keep
- `semivowel --distinct_from--> vowel` — **add this instead of the is_a**

### 🟢 APPROVE all remaining (after rejections above)

Bulk approve all relations not listed in reject/flag sections.
Estimated approved: ~300–320 after rejections.

---

## Summary for apply_ling_pass3_review.py

**Hard rejects by exact match:**
```
reject("bilabial", "is_a", "manner of articulation")
reject("phrase", "contains", "sentence")       # from coda
reject("root", "contains", "stem")
reject("stem", "part_of", "root")
reject("articulatory features", "part_of", "velar")
reject("predicate logic", "contains", "propositional logic")
reject("semivowel", "is_a", "vowel")
reject("noun", "contains", "article")
reject("noun", "requires", "nominalization")
reject("verb", "contains", "phrase")
reject("verb", "contains", "phrase structure")
reject("grammar", "enables", "lexeme")
```

**Off-topic rejects (exact subject only — reject all from these subjects when object is off-topic):**
```
reject("sentence", "part_of", "text")
reject("verb", "enables", "tense")
reject("tense", "derived_from", "aspect")
reject("action", "related_to", "event")
reject("event", "contains", "state")
reject("state", "distinct_from", "process")
reject("noun", "related_to", "category")
reject("text", "contains", "sentence")
reject("linguistic unit", "is_a", "language")
reject("word form", "is_a", "form")
reject("adjective", "contains", "descriptive information")
```

**Low-confidence auto-reject:**
```
reject_below_confidence(0.36)
```

**Bulk approve remaining.**

---

## Rerun list (LLM errors — missing concepts)

These 17 concepts need a second pass (LLM returned no output):
affricate, voiced, alveolar, rime, mora, glottal, complement, head,
adverb, determiner, synonym, antonym, prototype, denotation, compositionality,
scope

Recommended: create `llm_ingest_ling_pass3b.py` with just these anchors.
