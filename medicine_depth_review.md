# Medicine Pass 2 (depth) — GPT HITL Review

610 proposed relations, 67 concepts. Strong yield overall.

---

## 🔴 Hard rejects — direction / ontology errors

| # | Relation | Reason |
|---|----------|--------|
| 1 | `nervous system --is_a--> central nervous system` | reversed — CNS is part of nervous system |
| 2 | `muscular system --is_a--> skeletal system` | wrong — separate systems |
| 3 | `chromosome --part_of--> mitochondrion` | wrong — chromosomes are nuclear; mitochondria have separate DNA |
| 4 | `chromosome --part_of--> chromatid` | reversed — chromatid is copy of a chromosome |
| 5 | `dementia --distinct_from--> alzheimer's disease` | wrong — Alzheimer's IS a type of dementia |
| 6 | `dementia --enables--> neuroplasticity` | wrong — dementia impairs neuroplasticity |
| 7 | `dementia --enables--> cognitive reserve` | wrong — dementia erodes cognitive reserve |
| 8 | `outcome --derived_from--> mortality` | reversed — mortality is a type of outcome |
| 9 | `diagnosis --enables--> morbidity` | wrong — diagnosis identifies morbidity, does not enable it |
| 10 | `morbidity --enables--> treatment` | reversed — treatment manages morbidity |
| 11 | `depression --used_for--> treatment` | reversed — treatment is used for depression |
| 12 | `homeostasis --part_of--> endocrine system` | wrong — endocrine system participates in homeostasis, not the other way |
| 13 | `respiration --part_of--> cardiovascular system` | wrong — respiration is a process of the respiratory system |
| 14 | `hormone --part_of--> pancreas` | wrong — pancreas produces hormones; hormones are not parts of pancreas |
| 15 | `hormone --part_of--> adrenal gland` | same — adrenal gland produces hormones |
| 16 | `lymphocyte --co_occurs_with--> t-cell` | wrong — T-cells ARE lymphocytes (is_a, not co_occurs_with) |
| 17 | `neurotransmitter --derived_from--> tyrosine` | too specific — only catecholamines, not all neurotransmitters |
| 18 | `schizophrenia --used_for--> psychiatric diagnosis` | reversed — psychiatric diagnosis is used for schizophrenia |

## 🔴 Field-contains-instance violations (GPT rule)

| Relation | Reason |
|----------|--------|
| `antibiotic --contains--> penicillin` | penicillin is an instance, not a component |
| `antibiotic --contains--> ceftriaxone` | same |
| `antibiotic --contains--> azithromycin` | same |

These should be `penicillin --is_a--> antibiotic` (not in this batch, add to Pass 3).

---

## 🟡 Borderline — defer to GPT

| Relation | Note |
|----------|------|
| `vaccine --contains--> toxin` | valid for toxoid vaccines, misleading as general rule — suggest reject |
| `epigenetics --is_a--> genomics` | epigenetics is a subfield of genetics, not genomics specifically — suggest: `epigenetics --part_of--> genetics` |
| `bacteria --part_of--> ecosystem` | too ecological for medicine domain — suggest reject |

---

## 🟢 Approve all remaining after above rejects

Bulk approve. ~560-570 expected.

---

## Summary for apply_medicine_depth_review.py

```python
# Direction errors
reject("nervous system",   "is_a",         "central nervous system")
reject("muscular system",  "is_a",         "skeletal system")
reject("chromosome",       "part_of",      "mitochondrion")
reject("chromosome",       "part_of",      "chromatid")
reject("dementia",         "distinct_from","alzheimer's disease")
reject("dementia",         "enables",      "neuroplasticity")
reject("dementia",         "enables",      "cognitive reserve")
reject("outcome",          "derived_from", "mortality")
reject("diagnosis",        "enables",      "morbidity")
reject("morbidity",        "enables",      "treatment")
reject("depression",       "used_for",     "treatment")
reject("homeostasis",      "part_of",      "endocrine system")
reject("respiration",      "part_of",      "cardiovascular system")
reject("hormone",          "part_of",      "pancreas")
reject("hormone",          "part_of",      "adrenal gland")
reject("lymphocyte",       "co_occurs_with","t-cell")
reject("neurotransmitter", "derived_from", "tyrosine")
reject("schizophrenia",    "used_for",     "psychiatric diagnosis")

# Field-contains-instance
reject("antibiotic",       "contains",     "penicillin")
reject("antibiotic",       "contains",     "ceftriaxone")
reject("antibiotic",       "contains",     "azithromycin")

# Borderline rejects
reject("vaccine",          "contains",     "toxin")
reject("epigenetics",      "is_a",         "genomics")
reject("bacteria",         "part_of",      "ecosystem")

reject_below_confidence(0.36)
bulk_approve_remaining()
```
