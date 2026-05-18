# Linguistics Depth Pass — HITL Review
**Date:** 2026-05-08  
**Script:** llm_ingest_ling_depth.py  
**Model:** llama3:8b  
**Total proposed:** 357 relations across 42 concepts (2 LLM errors: allophone, language acquisition)  
**Pipeline:** Tier 2 → review → promote to relations_aggregated

---

## Instructions for reviewer

For each relation below, mark:
- **APPROVE** — factually correct, well-established
- **REJECT** — wrong, misleading, or too vague
- **FIX** — correct the subject/predicate/object and approve the corrected form

Pre-flagged concerns from Claude are marked ⚠️.

---

## PHONOLOGY

### phoneme (14 relations)
```
phoneme --is_a--> sound unit [0.98]
phoneme --part_of--> linguistics [0.95]
phoneme --contains--> distinctive feature [0.92]
phoneme --requires--> auditory perception [0.9]
phoneme --enables--> language production [0.95]
phoneme --part_of--> speech sound [0.93]
phoneme --distinct_from--> allophone [0.9]
phoneme --co_occurs_with--> morpheme [0.85]
phoneme --related_to--> syllable [0.8]
phoneme --part_of--> word [0.9]
phoneme --contains--> phonetic transcription [0.95]
phoneme --used_for--> speech recognition [0.9]
phoneme --part_of--> language processing [0.95]
phoneme --related_to--> articulation [0.8]
```
⚠️ `phoneme --part_of--> speech sound` — direction is reversed; a phoneme IS a speech sound (is_a), not part_of.  
⚠️ `phoneme --part_of--> word` — phonemes combine to form words; part_of is arguably correct but direction is awkward.

### syllable (11 relations)
```
syllable --part_of--> morphology [0.95]
syllable --contains--> vowel [0.92]
syllable --contains--> consonant [0.9]
syllable --requires--> nucleus [0.8]
syllable --co_occurs_with--> foot [0.7]
syllable --distinct_from--> phoneme [0.6]
syllable --part_of--> phrase [0.5]
syllable --contains--> rime [0.45]
syllable --contains--> coda [0.4]
syllable --related_to--> syntactic structure [0.35]
syllable --used_for--> word formation [0.3]
```
⚠️ `syllable --part_of--> phrase` — syllables are sub-word units; phrases are above-word units. Incorrect level.  
⚠️ `syllable --related_to--> syntactic structure [0.35]` — very low confidence, cross-domain noise.  
⚠️ `syllable --used_for--> word formation [0.3]` — very low confidence; syllables are phonological, not morphological units.

### intonation (13 relations)
```
intonation --part_of--> phonology [0.95]
intonation --contains--> pitch [0.97]
intonation --requires--> tone [0.96]
intonation --enables--> meaning [0.99]
intonation --co_occurs_with--> stress [0.94]
intonation --distinct_from--> rhythm [0.92]
intonation --part_of--> speech [0.98]
intonation --related_to--> accent [0.95]
intonation --used_for--> communication [0.97]
pitch --part_of--> intonation [0.99]
stress --co_occurs_with--> intonation [0.96]
rhythm --distinct_from--> intonation [0.94]
accent --related_to--> intonation [0.93]
```
⚠️ `intonation --requires--> tone` — in tone languages yes, but intonation and tone are distinct phenomena. Should be `related_to`.

### phonotactics (5 relations)
```
phonotactics --part_of--> phonology [0.95]
phonotactics --requires--> phonemes [0.9]
phonotactics --distinct_from--> morphology [0.7]
phonotactics --co_occurs_with--> orthography [0.75]
morphology --distinct_from--> phonotactics [0.7]
```

### prosody (7 relations)
```
prosody --part_of--> phonetics [0.95]
prosody --co_occurs_with--> morphology [0.85]
prosody --distinct_from--> phonology [0.8]
prosody --contains--> rhythm [0.95]
prosody --related_to--> discourse structure [0.8]
prosody --used_for--> language teaching [0.85]
prosody --part_of--> speech communication [0.95]
```
⚠️ `prosody --part_of--> phonetics` — prosody is a sub-field of PHONOLOGY, not phonetics specifically.  
⚠️ `prosody --distinct_from--> phonology` — WRONG: prosody IS a branch of phonology, not distinct from it. REJECT.

---

## MORPHOLOGY

### morpheme (13 relations)
```
morpheme --is_a--> linguistic unit [0.95]
morpheme --part_of--> morphology [0.9]
morpheme --contains--> phonemes [0.85]
morpheme --requires--> syllable structure [0.8]
morpheme --enables--> word formation [0.75]
morpheme --part_of--> phrase [0.7]
morpheme --co_occurs_with--> syllable [0.65]
morpheme --distinct_from--> lexeme [0.6]
morpheme --related_to--> semantics [0.55]
morpheme --part_of--> sentence [0.5]
morpheme --enables--> inflectional morphology [0.35]
morpheme --part_of--> phrase structure grammar [0.3]
morpheme --co_occurs_with--> prefix [0.25]
```
⚠️ `morpheme --enables--> inflectional morphology [0.35]` — very low confidence; also somewhat circular.  
⚠️ `morpheme --part_of--> phrase structure grammar [0.3]` — very low confidence; reject.  
⚠️ `morpheme --co_occurs_with--> prefix [0.25]` — very low confidence.

### affix (13 relations)
```
affix --part_of--> morphology [0.95]
affix --contains--> root [0.92]
affix --requires--> stem [0.9]
affix --enables--> derivation [0.96]
affix --co_occurs_with--> prefix [0.85]
affix --distinct_from--> infix [0.95]
affix --part_of--> word formation [0.93]
affix --related_to--> inflectional morphology [0.9]
affix --used_for--> language acquisition [0.92]
affix --part_of--> lexicon [0.9]
affix --contains--> suffix [0.92]
affix --requires--> syllable structure [0.95]
affix --enables--> semantic change [0.98]
```
⚠️ `affix --contains--> root` — an affix attaches TO a root; it doesn't contain a root. REJECT or FIX to `affix --requires--> root`.

### inflection (4 relations)
```
inflection --part_of--> grammar [0.95]
inflection --contains--> affix [0.92]
inflection --distinct_from--> derivation [0.93]
inflection --co_occurs_with--> syntax [0.91]
```

### derivation (5 relations)
```
derivation --part_of--> morphology [0.95]
derivation --contains--> affixation [0.8]
derivation --distinct_from--> compounding [0.9]
derivation --co_occurs_with--> inflection [0.85]
derivation --contains--> prefixation [0.85]
```

### compounding (4 relations)
```
compounding --part_of--> morphology [0.95]
compounding --co_occurs_with--> blending [0.85]
compounding --distinct_from--> conversion [0.9]
compounding --contains--> morphemes [0.92]
```

### paradigm (13 relations)
```
paradigm --is_a--> linguistic concept [0.9]
paradigm --part_of--> linguistics [0.95]
paradigm --contains--> schema [0.8]
paradigm --requires--> grammar [0.85]
paradigm --enables--> prediction [0.9]
paradigm --distinct_from--> model [0.7]
paradigm --co_occurs_with--> pattern [0.6]
paradigm --related_to--> syntax [0.8]
paradigm --part_of--> semiotics [0.9]
schema --contains--> element [0.7]
grammar --requires--> rule [0.8]
pattern --co_occurs_with--> structure [0.6]
semiotics --part_of--> linguistics [0.9]
```
⚠️ `paradigm --contains--> schema` — 'schema' here is cognitive psychology term, not linguistics-paradigm. Ambiguous. Consider rejecting.  
⚠️ `schema --contains--> element [0.7]` — too generic, not linguistics-specific. REJECT.  
⚠️ `grammar --requires--> rule [0.8]` — probably already in DB; also generic.  
⚠️ `pattern --co_occurs_with--> structure [0.6]` — too generic. REJECT.  
⚠️ `semiotics --part_of--> linguistics [0.9]` — debatable; semiotics is a broader field. Should be `related_to`.

---

## SYNTAX

### phrase (14 relations)
```
phrase --is_a--> linguistic unit [0.98]
phrase --contains--> word [0.97]
phrase --requires--> syntax [0.96]
phrase --enables--> semantics [0.95]
phrase --co_occurs_with--> clause [0.94]
phrase --distinct_from--> sentence fragment [0.93]
phrase --part_of--> discourse [0.92]
phrase --contains--> morpheme [0.91]
phrase --related_to--> intonation [0.9]
phrase --used_for--> communication [0.89]
phrase --part_of--> text [0.88]
phrase --contains--> syllable [0.87]
phrase --requires--> grammar [0.86]
phrase --enables--> pragmatics [0.85]
```
⚠️ `phrase --contains--> syllable` — syllables are sub-word; phrases are above-word. Wrong level. REJECT.

### clause (13 relations)
```
clause --is_a--> sentence [0.98]
clause --contains--> phrase [0.92]
clause --requires--> verb [0.96]
clause --requires--> noun [0.94]
clause --part_of--> text [0.97]
clause --related_to--> phrase structure rules [0.91]
clause --distinct_from--> phrase [0.99]
independent clause --is_a--> clause [0.98]
clause --contains--> subordinate clause [0.95]
clause --requires--> punctuation [0.93]
sentence --part_of--> text [0.96]
phrase --part_of--> clause [0.94]
verb phrase --contains--> clause [0.92]
```
⚠️ `clause --is_a--> sentence` — reversed: a sentence can contain clauses; a clause is not a sentence. FIX to `clause --part_of--> sentence`.  
⚠️ `clause --requires--> punctuation` — punctuation is orthographic, not syntactic. REJECT.  
⚠️ `verb phrase --contains--> clause` — a verb phrase doesn't contain a clause; it may contain a clausal complement in some analyses. Debatable.

### constituent (13 relations)
```
constituent --is_a--> linguistic unit [0.9]
constituent --part_of--> phrase structure grammar [0.85]
constituent --contains--> word [0.95]
constituent --requires--> syntactic category [0.9]
constituent --enables--> phrase structure rules [0.8]
constituent --distinct_from--> morpheme [0.9]
constituent --co_occurs_with--> dependency relation [0.85]
constituent --part_of--> treebank [0.8]
phrase structure grammar --contains--> constituent [0.9]
syntactic category --used_for--> constituent [0.95]
phrase structure rules --related_to--> constituent [0.9]
sentence --contains--> constituent [0.95]
morpheme --distinct_from--> constituent [0.9]
```
⚠️ `syntactic category --used_for--> constituent` — direction is odd; syntactic categories DEFINE/LABEL constituents. Consider `syntactic category --related_to--> constituent`.

### word order (10 relations)
```
word order --is_a--> linguistic structure [0.98]
word order --part_of--> sentence syntax [0.95]
word order --contains--> phrase [0.92]
word order --requires--> morphology [0.96]
word order --enables--> sentence semantics [0.94]
word order --related_to--> dependency grammar [0.93]
word order --co_occurs_with--> pragmatics [0.9]
morphology --enables--> word formation [0.97]
sentence syntax --contains--> phrase structure rules [0.98]
dependency grammar --related_to--> tree diagram [0.96]
```
⚠️ `word order --contains--> phrase` — word order is a property/pattern, it doesn't contain phrases. FIX to `word order --requires--> phrase`.

### dependency (8 relations)
```
dependency --is_a--> linguistic relation [0.9]
dependency --part_of--> syntax [0.95]
dependency --contains--> head [0.8]
dependency --contains--> dependent [0.85]
dependency --requires--> phrase structure grammar [0.9]
dependency --distinct_from--> constituent [0.7]
dependency --co_occurs_with--> movement [0.6]
dependency --related_to--> phrase structure rules [0.8]
```
⚠️ `dependency --requires--> phrase structure grammar` — dependency grammar is an ALTERNATIVE to phrase structure grammar, not dependent on it. REJECT or FIX to `distinct_from`.

### tense (6 relations)
```
tense --contains--> aspect [0.92]
tense --requires--> verb [0.98]
tense --related_to--> mood [0.9]
tense --distinct_from--> aspect [0.85]
tense --co_occurs_with--> modality [0.9]
aspect --part_of--> tense [0.95]
```
⚠️ `tense --contains--> aspect [0.92]` AND `tense --distinct_from--> aspect [0.85]` — CONTRADICTORY. Something can't both contain and be distinct from the same thing. REJECT one.  
⚠️ `aspect --part_of--> tense [0.95]` — also from the aspect batch. Aspect and tense are related but separate categories. This is language-specific (some languages fuse them, others don't). Mark as debatable.

### aspect (5 relations)
```
aspect --contains--> tense [0.92]
aspect --co_occurs_with--> mood [0.85]
aspect --distinct_from--> modality [0.9]
tense --part_of--> aspect [0.95]
aktionsart --related_to--> aspect [0.92]
```
⚠️ `aspect --contains--> tense [0.92]` — same circularity as above. If tense contains aspect AND aspect contains tense, both can't be right.

---

## SEMANTICS

### meaning (10 relations)
```
meaning --is_a--> semantics [0.99]
meaning --part_of--> linguistics [0.98]
meaning --contains--> sense [0.97]
meaning --requires--> reference [0.96]
meaning --enables--> interpretation [0.95]
meaning --distinct_from--> form [0.94]
meaning --co_occurs_with--> context [0.93]
sense --part_of--> meaning [0.92]
meaning --related_to--> pragmatics [0.87]
pragmatics --co_occurs_with--> meaning [0.86]
```

### entailment (12 relations)
```
entailment --is_a--> inference [0.98]
entailment --part_of--> pragmatics [0.95]
entailment --contains--> logical consequence [0.92]
entailment --requires--> premise [0.96]
entailment --enables--> deduction [0.94]
entailment --co_occurs_with--> implication [0.93]
entailment --distinct_from--> equivocation [0.91]
entailment --related_to--> modus ponens [0.97]
entailment --used_for--> argumentation [0.99]
entailment --requires--> truth value [0.96]
entailment --related_to--> consequence [0.97]
entailment --co_occurs_with--> inference rule [0.93]
```
⚠️ `entailment --part_of--> pragmatics` — entailment is a SEMANTICS concept, not pragmatics. FIX to `part_of --> semantics`.  
⚠️ `entailment --used_for--> argumentation [0.99]` — argumentation is rhetoric/logic; entailment belongs to formal semantics. Debatable.

### semantic role (8 relations)
```
semantic role --is_a--> linguistic concept [0.9]
semantic role --part_of--> semantics [0.95]
semantic role --contains--> argument structure [0.8]
semantic role --requires--> lexical semantics [0.85]
semantic role --co_occurs_with--> thematic relation [0.8]
semantic role --related_to--> event structure [0.9]
semantic role --contains--> agentive role [0.8]
semantic role --distinct_from--> pragmatics [0.7]
```

### presupposition (9 relations)
```
presupposition --is_a--> linguistic concept [0.99]
presupposition --part_of--> pragmatics [0.98]
presupposition --contains--> proposition [0.97]
presupposition --requires--> context [0.96]
presupposition --enables--> inference [0.95]
presupposition --co_occurs_with--> implication [0.94]
presupposition --distinct_from--> conclusion [0.93]
presupposition --used_for--> predictive modeling [0.9]
presupposition --part_of--> formal semantics [0.89]
```
⚠️ `presupposition --used_for--> predictive modeling [0.9]` — 'predictive modeling' is ML/statistics, not linguistics. REJECT.

### reference (12 relations)
```
reference --is_a--> linguistic concept [0.99]
reference --part_of--> semantics [0.98]
reference --contains--> antecedent [0.97]
reference --requires--> context [0.96]
reference --enables--> anaphora [0.95]
reference --distinct_from--> deixis [0.94]
reference --co_occurs_with--> pronoun [0.93]
reference --related_to--> coreference [0.92]
reference --part_of--> discourse analysis [0.91]
antecedent --contains--> noun phrase [0.9]
context --requires--> situation [0.89]
deixis --distinct_from--> inference [0.87]
```
⚠️ `reference --distinct_from--> deixis` — debatable; deictic expressions ARE a type of reference. Consider `related_to`.

### implicature (7 relations)
```
implicature --part_of--> pragmatics [0.95]
implicature --contains--> inference [0.8]
implicature --requires--> context [0.9]
implicature --co_occurs_with--> presupposition [0.65]
implicature --distinct_from--> connotation [0.8]
implicature --requires--> shared knowledge [0.8]
implicature --enables--> cooperative communication [0.9]
```

---

## PRAGMATICS / DISCOURSE

### speech act (10 relations)
```
speech act --part_of--> language use [0.95]
speech act --contains--> utterance [0.92]
speech act --requires--> intention [0.96]
speech act --requires--> context [0.94]
speech act --enables--> communication [0.97]
speech act --distinct_from--> thought [0.91]
utterance --part_of--> speech act [0.99]
illocutionary force --contains--> speech act [0.98]
speech act --used_for--> social interaction [0.96]
speech act --co_occurs_with--> nonverbal communication [0.95]
```
⚠️ `illocutionary force --contains--> speech act` — reversed: illocutionary force is a COMPONENT of a speech act, not a container of it. FIX to `speech act --contains--> illocutionary force`.

### deixis (6 relations)
```
deixis --contains--> shifters [0.9]
deixis --requires--> contextual information [0.8]
deixis --co_occurs_with--> anaphora [0.7]
shifters --part_of--> deixis [0.9]
pronouns --contains--> shifters [0.8]
anaphora --distinct_from--> cataphora [0.7]
```

### context (11 relations)
```
context --part_of--> language use [0.95]
context --contains--> utterance [0.92]
context --requires--> shared knowledge [0.9]
context --enables--> inference [0.95]
context --related_to--> pragmatics [0.85]
context --distinct_from--> situation [0.9]
context --co_occurs_with--> discourse [0.8]
utterance --part_of--> text [0.95]
situation --distinct_from--> event [0.9]
discourse --co_occurs_with--> conversation [0.8]
language use --part_of--> communication [0.95]
```
⚠️ `context --distinct_from--> situation` — context and situation are closely related and often used interchangeably. Debatable.

### cohesion (7 relations)
```
cohesion --part_of--> discourse analysis [0.95]
cohesion --related_to--> sentence structure [0.85]
cohesion --distinct_from--> disjunction [0.8]
cohesion --co_occurs_with--> anaphora [0.9]
sentence structure --related_to--> grammar [0.85]
disjunction --distinct_from--> conjunction [0.8]
anaphora --co_occurs_with--> coreference [0.9]
```
⚠️ `cohesion --distinct_from--> disjunction` — disjunction is a logical/syntactic operator, not the opposite of textual cohesion. REJECT.

### coherence (6 relations)
```
coherence --part_of--> discourse analysis [0.95]
coherence --requires--> semantic processing [0.9]
coherence --distinct_from--> inconsistency [0.98]
coherence --requires--> contextual information [0.9]
coherence --used_for--> comprehension [0.98]
coherence --part_of--> language processing [0.95]
```

### anaphora (11 relations)
```
anaphora --part_of--> pragmatics [0.95]
anaphora --contains--> pronoun [0.98]
anaphora --requires--> antecedent [0.97]
anaphora --related_to--> coreference [0.94]
anaphora --distinct_from--> cataphora [0.93]
anaphora --co_occurs_with--> deixis [0.92]
anaphora --part_of--> discourse structure [0.91]
anaphora --contains--> referring expression [0.9]
anaphora --requires--> shared knowledge [0.89]
anaphora --enables--> efficient communication [0.88]
referring expression --part_of--> anaphora [0.86]
```

### genre (3 relations)
```
genre --distinct_from--> style [0.7]
genre --co_occurs_with--> register [0.65]
genre --enables--> characterization [0.4]
```
⚠️ `genre --enables--> characterization [0.4]` — very low confidence, vague. REJECT.

---

## HISTORICAL / TYPOLOGICAL

### language family (9 relations)
```
language family --part_of--> linguistics [0.95]
language family --contains--> language [0.98]
language family --requires--> genetic relationship [0.85]
language family --related_to--> dialect [0.75]
language family --distinct_from--> language isolate [0.85]
language family --part_of--> linguistic typology [0.95]
language family --contains--> branch [0.9]
language family --requires--> glottochronology [0.8]
language family --enables--> historical linguistics [0.95]
```
⚠️ `language family --requires--> glottochronology` — glottochronology is one contested method; language families are established through comparative method, not glottochronology specifically. FIX to `related_to`.

### cognate (3 relations)
```
cognate --contains--> etymology [0.85]
cognate --co_occurs_with--> borrowing [0.8]
cognate --contains--> morphology [0.8]
```
⚠️ `cognate --contains--> etymology` — cognates don't "contain" etymology; they have etymological relationships. FIX to `cognate --related_to--> etymology`.  
⚠️ `cognate --contains--> morphology` — wrong: morphology is a field/system, not something a cognate contains. REJECT.

### reconstruction (1 relation)
```
reconstruction --distinct_from--> etymology [0.7]
```
Very sparse — LLM nearly failed on this concept. Only 1 valid relation proposed.

### language change (6 relations)
```
language change --part_of--> historical linguistics [0.95]
language change --co_occurs_with--> societal change [0.8]
language change --distinct_from--> language acquisition [0.9]
language change --part_of--> sociolinguistics [0.95]
language change --derived_from--> linguistic typology [0.9]
societal change --co_occurs_with--> language change [0.8]
```
⚠️ `language change --derived_from--> linguistic typology` — language change is not derived from typology. They're related fields. FIX to `related_to`.

---

## SOCIOLINGUISTICS

### code-switching (6 relations)
```
code-switching --part_of--> bilingualism [0.85]
code-switching --co_occurs_with--> language mixing [0.8]
code-switching --part_of--> sociolinguistics [0.85]
code-switching --related_to--> identity [0.8]
code-switching --enables--> social cohesion [0.9]
code-switching --requires--> cognitive flexibility [0.85]
```
⚠️ `code-switching --requires--> cognitive flexibility` — this is a psycholinguistic claim, debatable as a hard requirement.

### pidgin (10 relations)
```
pidgin --is_a--> language [0.98]
pidgin --part_of--> contact language [0.95]
pidgin --contains--> lexicon [0.97]
pidgin --enables--> communication [0.99]
pidgin --derived_from--> creole language [0.93]
pidgin --distinct_from--> native language [0.92]
pidgin --co_occurs_with--> lingua franca [0.91]
pidgin --contains--> vocabulary [0.98]
pidgin --enables--> trade and commerce [0.95]
pidgin --contains--> phonology [0.96]
```
⚠️ `pidgin --derived_from--> creole language` — **CRITICAL: REVERSED**. Creoles are derived FROM pidgins (a creole is a nativised pidgin), not the other way around. REJECT or FIX to `creole --derived_from--> pidgin`.

### creole (6 relations)
```
creole --is_a--> language [0.9]
creole --contains--> lexicon [0.95]
creole --requires--> simplification [0.8]
creole --enables--> communication [0.9]
lexicon --contains--> vocabulary [0.95]
communication --requires--> creole [0.85]
```
⚠️ `communication --requires--> creole` — **WRONG**: Communication does not require creole. REJECT.  
⚠️ `creole --requires--> simplification` — debatable; creolisation actually involves complexification in some respects.

### language contact (7 relations)
```
language contact --part_of--> linguistics [1.0]
language contact --contains--> borrowing [0.95]
language contact --enables--> code-switching [0.85]
language contact --co_occurs_with--> language shift [0.8]
language contact --related_to--> pidgin language [0.9]
language contact --requires--> cultural exchange [0.85]
language contact --enables--> language change [0.8]
```
⚠️ `language contact --requires--> cultural exchange` — language contact can happen without broad cultural exchange (e.g. conquest, trade). FIX to `co_occurs_with`.

---

## PSYCHOLINGUISTICS

### bilingualism (12 relations)
```
bilingualism --is_a--> language ability [0.9]
bilingualism --part_of--> multilingualism [0.8]
bilingualism --contains--> monolingualism [0.7]
bilingualism --requires--> language proficiency [0.9]
bilingualism --related_to--> code-switching [0.7]
bilingualism --distinct_from--> monolingualism [0.9]
bilingualism --co_occurs_with--> language contact [0.8]
bilingualism --used_for--> communication [0.9]
bilingual education --part_of--> education policy [0.9]
bilingual education --requires--> teacher training [0.8]
code-switching --related_to--> bilingualism [0.7]
multilingualism --contains--> polyglotism [0.7]
```
⚠️ `bilingualism --contains--> monolingualism` AND `bilingualism --distinct_from--> monolingualism` — CONTRADICTORY. A bilingual speaker is by definition NOT monolingual. REJECT `contains`.  
Note: `bilingual education --part_of--> education policy` and `bilingual education --requires--> teacher training` — 'bilingual education' is not in the anchor map, so these may be filtered out anyway.

### aphasia (14 relations)
```
aphasia --is_a--> language disorder [0.9]
aphasia --part_of--> neurolinguistics [0.95]
aphasia --contains--> anomia [0.8]
aphasia --requires--> brain damage [0.7]
aphasia --enables--> speech therapy [0.85]
aphasia --distinct_from--> dysarthria [0.9]
aphasia --co_occurs_with--> apraxia [0.8]
aphasia --related_to--> cerebral cortex [0.9]
aphasia --part_of--> clinical linguistics [0.95]
aphasia --contains--> agrammatism [0.8]
aphasia --requires--> neuroimaging [0.7]
aphasia --enables--> language assessment [0.85]
aphasia --distinct_from--> dysphasia [0.9]
aphasia --co_occurs_with--> cognitive impairment [0.8]
```
⚠️ `aphasia --enables--> speech therapy` — aphasia doesn't enable speech therapy; it REQUIRES it. FIX to `requires`.  
⚠️ `aphasia --requires--> neuroimaging` — neuroimaging is one diagnostic tool, not a requirement of aphasia itself.  
⚠️ `aphasia --enables--> language assessment` — same issue; aphasia doesn't enable language assessment, it occasions it.

---

## Summary of flagged issues for GPT

| # | Relation | Issue | Recommendation |
|---|----------|-------|----------------|
| 1 | `pidgin --derived_from--> creole language` | **CRITICAL: reversed causality** | REJECT |
| 2 | `communication --requires--> creole` | Factually wrong | REJECT |
| 3 | `tense --contains--> aspect` + `tense --distinct_from--> aspect` | Contradictory pair | REJECT both or keep only distinct_from |
| 4 | `aspect --contains--> tense` + `aspect --part_of--> tense` | Circular with tense | REJECT |
| 5 | `bilingualism --contains--> monolingualism` | Contradicts `distinct_from` below it | REJECT |
| 6 | `prosody --distinct_from--> phonology` | Prosody IS phonology | REJECT |
| 7 | `prosody --part_of--> phonetics` | Should be phonology | FIX |
| 8 | `entailment --part_of--> pragmatics` | Should be semantics | FIX |
| 9 | `clause --is_a--> sentence` | Reversed | FIX to `part_of` |
| 10 | `illocutionary force --contains--> speech act` | Reversed | FIX |
| 11 | `affix --contains--> root` | Wrong: affix attaches TO root | FIX to `requires` |
| 12 | `dependency --requires--> phrase structure grammar` | PSG is an alternative, not requirement | REJECT |
| 13 | `presupposition --used_for--> predictive modeling` | ML term, not linguistics | REJECT |
| 14 | `cohesion --distinct_from--> disjunction` | Wrong pairing | REJECT |
| 15 | `aphasia --enables--> speech therapy` | Should be `requires` | FIX |
| 16 | Any relation with confidence < 0.35 | Noise threshold | REJECT |
