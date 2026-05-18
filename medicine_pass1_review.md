# Medicine Pass 1 — GPT HITL Review

Dry-run output: 258 proposed relations, 20 concepts.
Most anchor-miss filtering will happen automatically. Focus review on conceptual errors.

---

## 🔴 Hard rejects — direction / ontology errors

| # | Relation | Reason |
|---|----------|--------|
| 1 | `medicine --distinct_from--> surgery` | surgery IS a branch of medicine, not distinct |
| 2 | `diagnosis --contains--> disease` | reversed — diagnosis identifies/requires disease |
| 3 | `molecular medicine --part_of--> biochemistry` | reversed — biochemistry is part of molecular medicine |
| 4 | `immunology --part_of--> inflammation response` | reversed — inflammation response is part of immunology |
| 5 | `disease --part_of--> patient outcome` | wrong concept — patient outcome is affected by disease |
| 6 | `medicine --part_of--> healthcare` | reversed — healthcare contains medicine |

## 🟡 Redundant pairs — keep one direction only

| Keep | Reject |
|------|--------|
| `biochemistry --contains--> enzymes` | `enzymes --part_of--> biochemistry` |
| `immune response --part_of--> immunology` | `immunology --contains--> immune response` (already have part_of) |
| `inflammation response --part_of--> immunology` → REJECT BOTH (see #4 above) | |
| `pharmacology --co_occurs_with--> biochemistry` | `biochemistry --co_occurs_with--> pharmacology` (duplicate) |

## 🟢 Approve all remaining after rejections above

Bulk approve. Anchor-miss filtering handles the rest (healthcare system, patient data,
disease management, medical expertise, pain relief etc. likely won't resolve).

---

## Summary for apply_medicine_review.py

```python
reject("medicine",          "distinct_from", "surgery")
reject("diagnosis",         "contains",      "disease")
reject("molecular medicine","part_of",       "biochemistry")
reject("immunology",        "part_of",       "inflammation response")
reject("disease",           "part_of",       "patient outcome")
reject("medicine",          "part_of",       "healthcare")
reject("enzymes",           "part_of",       "biochemistry")
reject_below_confidence(0.36)
bulk_approve_remaining()
```

Expected: ~220-230 approved after anchor filtering + rejects.
