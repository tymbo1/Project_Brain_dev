# Medicine Pass 3 — HITL Review
**Total proposed:** 566 relations across 83 concepts
**Model:** llama3:8b | **Date:** 2026-05-09

---

## Pre-flagged issues (Claude's analysis — confirm or override)

### Direction errors — REJECT

| Relation | Reason |
|---|---|
| `atrium --derived_from--> endocardium` | derived_from = embryological origin, not structural composition |
| `atrium --derived_from--> myocardium` | same — atrium is not derived from myocardium |
| `vein --part_of--> limb` | wrong direction: limbs contain veins |
| `vein --part_of--> organ` | wrong direction: organs contain veins |
| `expiration --enables--> lung` | wrong direction: lung enables expiration |
| `mucus --contains--> bronchus` | wrong direction: bronchus contains mucus |
| `cilia --used_for--> bronchus` | malformed: cilia clear bronchi, not used_for bronchus |
| `ischemia --contains--> myocardial infarction` | wrong: MI is a result of ischemia, not contained in it |
| `infarction --derived_from--> angiography` | angiography is a diagnostic tool, not origin of infarction |
| `infarction --derived_from--> computed tomography angiography` | same |
| `infarction --part_of--> stroke` | infarction can cause stroke but is not part_of stroke |
| `t cell --is_a--> effector t cell` | wrong direction: effector t cell is_a t cell |
| `immunoglobulin --part_of--> antibody` | wrong direction: antibody is made of immunoglobulins / immunoglobulin is_a antibody |
| `dna damage --requires--> uv radiation` | wrong: uv radiation causes DNA damage, not the reverse |
| `cell death --requires--> mitochondrial dysfunction` | wrong direction: mitochondrial dysfunction enables cell death |
| `antiviral --requires--> viral replication` | wrong: antiviral inhibits viral replication, not requires it |
| `hypotension --contains--> vasodilation` | wrong direction: vasodilation can cause hypotension |

### Process used as object in derived_from — REJECT

| Relation | Reason |
|---|---|
| `mitochondria --derived_from--> endosymbiosis` | endosymbiosis is a process, not an entity |
| `glucose --derived_from--> photosynthesis` | photosynthesis is a process |
| `allele --derived_from--> natural selection` | natural selection is a process |
| `lymphoma --derived_from--> genetic mutations` | genetic mutations is a process/plural event |
| `antidepressant --derived_from--> chemical synthesis` | chemical synthesis is a process |
| `antipsychotic --derived_from--> chemical synthesis` | same |

### Tautological or ontologically confused — REJECT

| Relation | Reason |
|---|---|
| `adrenaline --contains--> epinephrine` | adrenaline IS epinephrine (same molecule, different name) |
| `adrenaline --contains--> norepinephrine` | norepinephrine is a different molecule, not contained in adrenaline |
| `hypotension --contains--> low blood pressure` | tautological: hypotension = low blood pressure |
| `half-life --is_a--> biological_process` | half-life is a measure/concept, not a process |
| `angiogenesis --is_a--> biological_process` | should be `angiogenesis --is_a--> biological process` (underscore artefact); approve if anchor matches |
| `lymphoma --used_for--> diagnosis and treatment` | malformed: lymphoma is not used_for diagnosis |
| `adenoma --used_for--> endocrine function` | wrong: adenoma disrupts endocrine function, doesn't serve it |
| `delusion --used_for--> avoidance behavior` | delusion doesn't serve avoidance behavior |
| `cancer prevention --used_for--> patient treatment` | too abstract/recursive — not a clean ontological edge |

### Low-value / too atomic — REJECT

| Relation | Reason |
|---|---|
| `glucose --contains--> carbon atoms` | too atomic: elemental chemistry, not biological concept |
| `glucose --contains--> hydrogen atoms` | same |

### Low-yield concepts (0–2 valid relations)
These concepts may need manual seeds or Pass 3b recovery:
- `aorta`: 0 relations (all filtered — likely no anchor matches for aorta's objects)
- `transcription`: 1 relation only (`distinct_from translation`)
- `diastole`: 2 relations only
- `interleukin`: 1 relation only
- `interferon`: 2 relations
- `atp`: 3 relations

---

## Full relation list for GPT review

### [liver] — 10 valid
```
liver --part_of--> abdomen [0.98]
liver --contains--> hepatocytes [0.97]
liver --requires--> oxygen [0.96]
liver --requires--> glucose [0.95]
liver --enables--> detoxification [0.94]
liver --enables--> metabolism [0.93]
liver --part_of--> gastrointestinal system [0.92]
liver --distinct_from--> spleen [0.91]
liver --contains--> bile ducts [0.9]
liver --requires--> vitamin d [0.89]
```

### [kidney] — 10 valid
```
kidney --is_a--> organ [0.99]
kidney --contains--> nephrons [0.98]
kidney --requires--> blood supply [0.97]
kidney --derived_from--> embryonic tissue [0.94]
kidney cortex --part_of--> kidney [0.98]
medulla --part_of--> kidney [0.97]
kidney --distinct_from--> liver [0.96]
kidney --distinct_from--> pancreas [0.95]
kidney --requires--> renal blood flow [0.94]
kidney --used_for--> regulation of blood pressure [0.92]
```

### [lung] — 11 valid
```
lung --is_a--> organ [0.99]
lung --part_of--> thoracic cavity [0.95]
lung --contains--> alveoli [0.98]
lung --requires--> oxygen [0.97]
lung --enables--> respiration [0.96]
alveoli --part_of--> lung [0.98]
blood vessels --part_of--> lung [0.97]
lung --contains--> bronchioles [0.96]
lung --distinct_from--> heart [0.98]
lung --requires--> inspiration [0.96]
expiration --enables--> lung [0.95]   ← REJECT: wrong direction
```

### [pancreas] — 8 valid
```
pancreas --part_of--> abdomen [0.95]
pancreas --contains--> acini [0.98]
pancreas --requires--> carbohydrates [0.97]
pancreas --requires--> proteins [0.96]
pancreas --requires--> fats [0.95]
pancreas --enables--> digestion [0.99]
pancreas --contains--> islets of langerhans [0.97]
pancreas --distinct_from--> spleen [0.95]
```

### [brain] — 11 valid
```
brain --contains--> cerebral cortex [0.97]
brain --requires--> oxygen [0.96]
brain --requires--> glucose [0.95]
brain --contains--> hippocampus [0.94]
brain --part_of--> skull [0.93]
brain --requires--> water [0.92]
brain --enables--> thought [0.91]
brain --enables--> memory [0.9]
brain --distinct_from--> spinal cord [0.89]
brain --contains--> basal ganglia [0.88]
brain --requires--> vitamin b12 [0.86]
```

### [spinal cord] — 8 valid
```
spinal cord --is_a--> nervous tissue [0.99]
spinal cord --part_of--> vertebral column [0.98]
spinal cord --contains--> gray matter [0.97]
spinal cord --contains--> white matter [0.96]
spinal cord --requires--> blood supply [0.95]
spinal cord --enables--> motor function [0.94]
spinal cord --enables--> sensory function [0.93]
spinal cord --distinct_from--> brain [0.92]
```

### [hippocampus] — 5 valid
```
hippocampus --is_a--> brain structure [0.99]
hippocampus --part_of--> temporal lobe [0.98]
hippocampus --requires--> neurotransmitter release [0.96]
hippocampus --enables--> memory formation [0.95]
hippocampus --part_of--> cerebral cortex [0.94]
```

### [amygdala] — 5 valid
```
amygdala --is_a--> structure [0.99]
amygdala --contains--> neurons [0.98]
amygdala --requires--> glutamate [0.96]
amygdala --distinct_from--> hippocampus [0.93]
amygdala --requires--> dopamine [0.95]
```

### [cerebral cortex] — 10 valid
```
cerebral cortex --is_a--> brain structure [0.99]
cerebral cortex --part_of--> brain [0.98]
cerebral cortex --contains--> motor cortex [0.97]
cerebral cortex --contains--> sensory cortex [0.96]
cerebral cortex --requires--> oxygen [0.95]
cerebral cortex --requires--> glucose [0.94]
cerebral cortex --enables--> thought [0.93]
cerebral cortex --enables--> memory [0.92]
cerebral cortex --distinct_from--> spinal cord [0.91]
cerebral cortex --part_of--> central nervous system [0.9]
```

### [cerebellum] — 6 valid
```
cerebellum --is_a--> brain structure [0.99]
cerebellum --part_of--> central nervous system [0.98]
cerebellum --requires--> neurotransmitters [0.96]
cerebellum --enables--> motor coordination [0.95]
cerebellum --derived_from--> embryonic tissue [0.93]
cerebellum --distinct_from--> basal ganglia [0.92]
```

### [ventricle] — 6 valid
```
ventricle --part_of--> heart [0.98]
ventricle --contains--> myocardium [0.97]
ventricle --requires--> blood flow [0.96]
ventricle --enables--> pumping action [0.95]
ventricle --distinct_from--> atrium [0.93]
ventricle --enables--> cardiac output [0.89]
```

### [atrium] — 6 valid (2 flagged)
```
atrium --part_of--> heart [0.98]
atrium --contains--> pulmonary veins [0.97]
atrium --requires--> blood flow [0.96]
atrium --derived_from--> endocardium [0.93]   ← REJECT: structural not embryological
atrium --distinct_from--> ventricle [0.91]
atrium --derived_from--> myocardium [0.85]   ← REJECT: same reason
```

### [capillary] — 12 valid
```
capillary --part_of--> microcirculation [0.95]
capillary --contains--> red blood cells [0.98]
capillary --contains--> white blood cells [0.97]
capillary --requires--> blood pressure [0.96]
capillary --requires--> oxygen [0.95]
capillary --enables--> gas exchange [0.99]
capillary --used_for--> tissue oxygenation [0.98]
capillary --distinct_from--> artery [0.96]
capillary --distinct_from--> vein [0.95]
capillary --part_of--> circulatory system [0.99]
capillary --requires--> nitric oxide [0.95]
capillary --enables--> vasodilation [0.99]
```

### [aorta] — 0 valid
All 11 LLM relations filtered (anchor misses). Mark as Pass 3b recovery candidate.

### [vein] — 9 valid (2 flagged)
```
vein --contains--> blood plasma [0.97]
vein --requires--> arterial pressure [0.96]
vein --enables--> oxygen delivery [0.95]
vein --distinct_from--> artery [0.93]
vein --part_of--> limb [0.91]   ← REJECT: wrong direction
vein --contains--> red blood cells [0.9]
vein --enables--> waste removal [0.88]
vein --part_of--> organ [0.87]   ← REJECT: wrong direction
vein --distinct_from--> lymphatic vessel [0.86]
```

### [mitochondria] — 8 valid (1 flagged)
```
mitochondria --is_a--> organelle [0.99]
mitochondria --part_of--> cell [0.95]
mitochondrion --contains--> dna [0.8]
mitochondria --requires--> oxygen [0.9]
mitochondria --requires--> glucose [0.85]
mitochondria --used_for--> energy production [0.95]
mitochondria --derived_from--> endosymbiosis [0.9]   ← REJECT: process not entity
mitochondria --distinct_from--> lysosome [0.8]
```

### [nucleus] — 7 valid
```
nucleus --contains--> chromatin [0.98]
nucleus --contains--> histones [0.97]
nucleus --requires--> dna [0.96]
nucleus --requires--> enzymes [0.94]
nucleus --enables--> transcription [0.99]
nucleus --enables--> replication [0.98]
nucleus --distinct_from--> mitochondria [0.95]
```

### [ribosome] — 9 valid
```
ribosome --is_a--> cellular structure [0.99]
ribosome --part_of--> cytoplasm [0.98]
ribosome --contains--> rna [0.97]
ribosome --requires--> mrna [0.96]
ribosome --enables--> protein synthesis [0.94]
ribosome --used_for--> translation [0.93]
ribosome --distinct_from--> lysosome [0.92]
ribosome --derived_from--> nucleus [0.91]
ribosome --part_of--> cell [0.9]
```

### [membrane] — 9 valid
```
membrane --part_of--> cell [0.98]
membrane --contains--> lipid [0.97]
membrane --requires--> proteins [0.96]
membrane --distinct_from--> cytoplasm [0.94]
membrane --part_of--> organelle [0.93]
membrane --contains--> ion channels [0.92]
membrane --requires--> lipid metabolism [0.91]
membrane --enables--> signal transduction [0.9]
membrane --distinct_from--> extracellular matrix [0.89]
```

### [glucose] — 10 valid (2 flagged)
```
glucose --is_a--> carbohydrate [0.99]
glucose --part_of--> blood plasma [0.95]
glucose --contains--> carbon atoms [0.98]   ← REJECT: too atomic
glucose --requires--> insulin [0.97]
glucose --enables--> cellular energy production [0.96]
glucose --used_for--> glycolysis [0.95]
glucose --derived_from--> photosynthesis [0.94]   ← REJECT: process
glucose --distinct_from--> fructose [0.93]
glucose --part_of--> glycogen [0.92]
glucose --contains--> hydrogen atoms [0.91]   ← REJECT: too atomic
```

### [insulin] — 8 valid
```
insulin --contains--> peptide chain [0.98]
insulin --requires--> glucose [0.96]
insulin --enables--> glycolysis [0.97]
insulin --used_for--> blood sugar regulation [0.99]
insulin --derived_from--> proinsulin [0.95]
insulin --distinct_from--> glucagon [0.98]
insulin --part_of--> endocrine system [0.96]
insulin --requires--> insulin receptor [0.97]
```

### [glucagon] — 7 valid
```
glucagon --contains--> amino acid sequence [0.98]
glucagon --requires--> camp-dependent protein kinase [0.97]
glucagon --enables--> glycogenolysis [0.96]
glucagon --derived_from--> proglucagon [0.94]
glucagon --distinct_from--> insulin [0.93]
glucagon --part_of--> endocrine system [0.92]
glucagon --contains--> peptide hormone [0.91]
```

### [atp] — 3 valid
```
atp --is_a--> nucleotide [0.99]
atp --distinct_from--> gtp [0.99]
atp --distinct_from--> nadh [0.96]
```
Mark as Pass 3b recovery candidate (very low yield).

### [cortisol] — 5 valid
```
cortisol --is_a--> steroid hormone [0.99]
cortisol --part_of--> adrenal gland [0.95]
cortisol --contains--> hydroxyl group [0.98]
cortisol --requires--> cholesterol [0.92]
cortisol --distinct_from--> aldosterone [0.98]
```

### [adrenaline] — 9 valid (2 flagged)
```
adrenaline --is_a--> hormone [0.99]
adrenaline --part_of--> endocrine system [0.98]
adrenaline --contains--> norepinephrine [0.95]   ← REJECT: different molecule
adrenaline --requires--> corticotropin-releasing hormone [0.97]
adrenaline --enables--> increased heart rate [0.96]
adrenaline --used_for--> fight or flight response [0.99]
adrenaline --distinct_from--> insulin [0.95]
adrenaline --part_of--> adrenal gland [0.99]
adrenaline --contains--> epinephrine [0.98]   ← REJECT: adrenaline = epinephrine
```

### [estrogen] — 12 valid
```
estrogen --is_a--> hormone [0.99]
estrogen --part_of--> endocrine system [0.95]
estrogen --contains--> estradiol [0.98]
estrogen --requires--> cholesterol [0.96]
estrogen --enables--> cell proliferation [0.97]
estrogen --used_for--> feminization [0.99]
estrogen --derived_from--> pregnenolone [0.95]
estrogen receptor --part_of--> cell nucleus [0.99]
estrogen receptor --requires--> hormone response element [0.96]
estrogen --part_of--> female reproductive system [0.95]
estrogen --contains--> estrone [0.98]
estrogen --enables--> menstruation [0.97]
```

### [testosterone] — 5 valid
```
testosterone --is_a--> hormone [0.99]
testosterone --part_of--> male reproductive system [0.95]
testosterone --requires--> chorionic gonadotropin [0.96]
testosterone --derived_from--> cholesterol [0.93]
testosterone --distinct_from--> estrogen [0.99]
```

### [alveolus] — 5 valid
```
alveolus --contains--> alveolar sac [0.98]
alveolus --requires--> oxygen [0.96]
alveolus --requires--> carbon dioxide [0.95]
alveolus --enables--> gas exchange [0.99]
alveolus --distinct_from--> bronchiole [0.98]
```

### [bronchus] — 11 valid (3 flagged)
```
bronchus --is_a--> respiratory tract [0.99]
bronchus --part_of--> lung [0.98]
bronchus --contains--> bronchiole [0.97]
bronchus --requires--> air [0.96]
bronchus --enables--> gas exchange [0.95]
bronchus --used_for--> breathing [0.94]
bronchiole --part_of--> bronchus [0.92]
mucus --contains--> bronchus [0.91]   ← REJECT: wrong direction
cilia --used_for--> bronchus [0.9]   ← REJECT: malformed
bronchus --distinct_from--> oesophagus [0.89]
airway --part_of--> respiratory tract [0.88]
```

### [apoptosis] — 6 valid
```
apoptosis --part_of--> cellular process [0.95]
apoptosis --contains--> caspase activation [0.98]
apoptosis --enables--> cell cycle regulation [0.96]
apoptosis --distinct_from--> necrosis [0.99]
apoptosis --part_of--> inflammation resolution [0.97]
apoptosis --enables--> tumor suppression [0.95]
```

### [ischemia] — 7 valid (1 flagged)
```
ischemia --is_a--> vascular disorder [0.98]
ischemia --part_of--> cardiovascular disease [0.95]
ischemia --requires--> adequate oxygen supply [0.96]
ischemia --enables--> tissue damage [0.99]
ischemia --distinct_from--> infarction [0.99]
ischemia --contains--> myocardial infarction [0.96]   ← REJECT: wrong; MI is result of ischemia
ischemia --requires--> normal blood pressure [0.95]
```

### [infarction] — 10 valid (3 flagged)
```
infarction --is_a--> vascular disorder [0.99]
infarction --part_of--> stroke [0.95]   ← REJECT: infarction is not part_of stroke
infarction --enables--> tissue damage [0.96]
infarction --derived_from--> angiography [0.94]   ← REJECT: diagnostic tool ≠ origin
infarction --distinct_from--> hemorrhage [0.93]
infarction --part_of--> cardiovascular disease [0.92]
infarction --contains--> ischemia [0.91]
infarction --enables--> organ dysfunction [0.89]
infarction --derived_from--> computed tomography angiography [0.87]   ← REJECT: same reason
infarction --distinct_from--> abscess [0.86]
```

### [macrophage] — 4 valid
```
macrophage --contains--> lysosome [0.98]
macrophage --requires--> chemokine [0.97]
macrophage --enables--> phagocytosis [0.99]
macrophage --derived_from--> monocyte [0.97]
```

### [leukocyte] — 7 valid
```
leukocyte --part_of--> blood [0.95]
leukocyte --contains--> cytoplasm [0.98]
leukocyte --requires--> oxygen [0.96]
leukocyte --used_for--> immunity [0.99]
leukocyte --distinct_from--> erythrocyte [0.99]
leukocyte --requires--> nutrients [0.96]
leukocyte --enables--> phagocytosis [0.97]
```

### [sarcoma] — 9 valid
```
sarcoma --is_a--> tumor [0.99]
sarcoma --part_of--> soft tissue [0.95]
sarcoma --requires--> angiogenic factors [0.97]
sarcoma --enables--> metastasis [0.96]
sarcoma --derived_from--> mesenchymal stem cells [0.98]
sarcoma --distinct_from--> carcinoma [0.99]
sarcoma --part_of--> connective tissue [0.95]
sarcoma --contains--> tumor cells [0.98]
sarcoma --distinct_from--> lymphoma [0.99]
```

### [lymphoma] — 9 valid (2 flagged)
```
lymphoma --part_of--> immune system [0.95]
lymphoma --contains--> lymphocytes [0.98]
lymphoma --requires--> immunodeficiency [0.92]
lymphoma --enables--> tumor growth [0.97]
lymphoma --used_for--> diagnosis and treatment [0.96]   ← REJECT: malformed
lymphoma --derived_from--> genetic mutations [0.93]   ← REJECT: process
lymphoma --distinct_from--> leukemia [0.94]
lymphoma --contains--> t cells [0.98]
lymphoma --requires--> viral infections [0.91]
```

### [adenoma] — 6 valid (1 flagged)
```
adenoma --is_a--> tumor [0.98]
adenoma --part_of--> gland [0.95]
adenoma --requires--> cell proliferation [0.97]
adenoma --used_for--> endocrine function [0.94]   ← REJECT: wrong direction
adenoma --distinct_from--> carcinoma [0.92]
adenoma --part_of--> organ [0.91]
```

### [analgesic] — 3 valid
```
analgesic --is_a--> medication [0.99]
analgesic --distinct_from--> anxiolytic [0.93]
analgesic --part_of--> pharmacotherapy [0.91]
```

### [antidepressant] — 6 valid (1 flagged)
```
antidepressant --part_of--> medication [0.95]
antidepressant --contains--> selective serotonin reuptake inhibitor [0.9]
antidepressant --requires--> prescription [0.8]
antidepressant --used_for--> treatment of depression [0.9]
antidepressant --derived_from--> chemical synthesis [0.8]   ← REJECT: process
antidepressant --distinct_from--> stimulant [0.6]
```

### [antiviral] — 7 valid (1 flagged)
```
antiviral --is_a--> therapeutic agent [0.98]
antiviral --part_of--> virology [0.95]
antiviral --requires--> viral replication [0.97]   ← REJECT: wrong direction
antiviral --enables--> immune response [0.96]
antiviral --derived_from--> natural products [0.94]
antiviral --part_of--> pharmacotherapy [0.98]
antiviral --contains--> interferons [0.91]
```

### [ligand] — 8 valid
```
ligand --is_a--> molecule [0.99]
ligand --part_of--> biomolecule [0.95]
ligand --contains--> atom [0.98]
ligand --requires--> binding site [0.97]
ligand --enables--> receptor activation [0.96]
ligand --used_for--> signal transduction [0.95]
ligand --contains--> functional group [0.92]
ligand --enables--> cell signaling pathway [0.9]
```

### [agonist] — 13 valid
```
agonist --is_a--> ligand [0.98]
agonist --part_of--> signal transduction pathway [0.97]
agonist --contains--> binding site [0.96]
agonist --requires--> receptor [0.95]
agonist --enables--> cell signaling [0.94]
agonist --derived_from--> natural product [0.92]
agonist --distinct_from--> antagonist [0.91]
agonist --is_a--> small molecule [0.9]
agonist --part_of--> pharmacopeia [0.89]
agonist --contains--> functional group [0.88]
agonist --requires--> enzymatic activity [0.87]
agonist --enables--> protein phosphorylation [0.86]
agonist --used_for--> research study [0.85]
```

### [antagonist] — 7 valid
```
antagonist --part_of--> pharmacological treatment [0.95]
antagonist --contains--> molecule [0.98]
antagonist --requires--> enzymatic activity [0.96]
antagonist --used_for--> disease treatment [0.99]
antagonist --derived_from--> natural product [0.95]
antagonist --distinct_from--> agonist [0.98]
antagonist --contains--> functional group [0.97]
```

### [half-life] — 7 valid (1 flagged)
```
half-life --is_a--> biological_process [0.99]   ← REJECT: half-life is a measure, not a process
half-life --part_of--> pharmacokinetics [0.95]
half-life --contains--> radioactive decay [0.98]
half-life --requires--> initial concentration [0.96]
half-life --distinct_from--> shelf life [0.95]
half-life --is_a--> physiological process [0.99]
half-life --part_of--> pharmacology [0.96]
```

### [immunoglobulin] — 6 valid (1 flagged)
```
immunoglobulin --is_a--> protein [0.99]
immunoglobulin --part_of--> antibody [0.95]   ← REJECT: wrong direction; immunoglobulin is_a antibody
immunoglobulin --contains--> heavy chain [0.98]
immunoglobulin --used_for--> immune response [0.98]
immunoglobulin --derived_from--> b cell receptor [0.97]
immunoglobulin --distinct_from--> tumor necrosis factor [0.95]
```

### [b cell] — 7 valid
```
b cell --part_of--> immune system [0.95]
b cell --requires--> antigen presentation [0.97]
b cell --enables--> antibody production [0.99]
b cell --used_for--> immune response [0.98]
b cell --distinct_from--> t cell [0.95]
b cell --is_a--> leukocyte [0.99]
b cell --part_of--> lymphoid tissue [0.96]
```

### [t cell] — 7 valid (1 flagged)
```
t cell --part_of--> immune system [0.95]
t cell --requires--> antigen presentation [0.97]
t cell --requires--> co-stimulation [0.96]
t cell --enables--> cell-mediated immunity [0.99]
t cell --distinct_from--> b cell [0.99]
t cell --is_a--> effector t cell [0.95]   ← REJECT: wrong direction
t cell --part_of--> thymus [0.94]
```

### [interferon] — 2 valid
```
interferon --is_a--> cytokine [0.99]
interferon --contains--> glycoprotein [0.98]
```
Low yield. Consider Pass 3b recovery.

### [interleukin] — 1 valid
```
interleukin --distinct_from--> chemokine [0.93]
```
Very low yield. Pass 3b recovery candidate.

### [allele] — 12 valid (1 flagged)
```
allele --is_a--> genetic variant [0.99]
allele --part_of--> chromosome [0.95]
allele --contains--> dna sequence [0.98]
allele --requires--> genetic information [0.96]
allele --enables--> protein synthesis [0.97]
allele --used_for--> inheritance [0.94]
allele --distinct_from--> genotype [0.92]
allele --part_of--> genome [0.91]
allele --contains--> codon [0.9]
allele --requires--> enzymes [0.89]
allele --used_for--> evolutionary adaptation [0.87]
allele --derived_from--> natural selection [0.86]   ← REJECT: process
```

### [gene expression] — 11 valid
```
gene expression --is_a--> biological process [0.99]
gene expression --part_of--> cellular activity [0.95]
gene expression --contains--> messenger rna [0.98]
gene expression --requires--> transcription factors [0.97]
gene expression --enables--> protein synthesis [0.96]
gene expression --used_for--> cell signaling [0.94]
gene expression --derived_from--> genetic information [0.93]
gene expression --distinct_from--> epigenetic modification [0.92]
gene expression --part_of--> metabolic pathway [0.91]
gene expression --contains--> promoter region [0.9]
gene expression --requires--> chromatin remodeling [0.89]
```

### [transcription] — 1 valid
```
transcription --distinct_from--> translation [0.93]
```
Very low yield. Pass 3b recovery candidate.

### [translation] — 6 valid
```
translation --contains--> mrna [0.96]
translation --requires--> ribosomes [0.95]
translation --enables--> protein production [0.93]
translation --distinct_from--> transcription [0.92]
translation --part_of--> gene expression regulation [0.91]
translation --derived_from--> genetic information [0.89]
```

### [point mutation] — 5 valid
```
point mutation --part_of--> genetic change [0.95]
point mutation --contains--> dna sequence [0.98]
point mutation --requires--> enzymatic activity [0.96]
point mutation --used_for--> evolutionary adaptation [0.94]
point mutation --part_of--> genetic variation [0.91]
```

### [telomere] — 5 valid
```
telomere --requires--> enzymatic activity [0.96]
telomere --enables--> chromosomal stability [0.95]
telomere --used_for--> cellular aging [0.94]
telomere --distinct_from--> centromere [0.92]
telomere --part_of--> nuclear envelope [0.91]
```

### [centromere] — 5 valid
```
centromere --contains--> histone protein [0.98]
centromere --enables--> chromosomal segregation [0.96]
centromere --distinct_from--> telomere [0.99]
centromere --part_of--> nuclear envelope [0.94]
centromere --requires--> histone modification [0.95]
```

### [karyotype] — 7 valid
```
karyotype --part_of--> genetic information [0.95]
karyotype --contains--> chromosomes [0.99]
karyotype --requires--> dna sequencing [0.96]
karyotype --enables--> genetic diagnosis [0.97]
chromosomes --part_of--> karyotype [0.99]
karyotype --distinct_from--> phenotype [0.93]
genetic information --part_of--> genome [0.95]
```

### [dendrite] — 7 valid
```
dendrite --part_of--> neuron [1.0]
dendrite --contains--> synapse [0.95]
dendrite --requires--> axon [0.9]
dendrite --requires--> neurotransmitter release [0.85]
dendrite --enables--> signal transmission [0.98]
dendrite --distinct_from--> axon [1.0]
dendrite --contains--> spine apparatus [0.9]
```

### [axon] — 4 valid
```
axon --contains--> microtubules [0.98]
axon --requires--> neurotransmitters [0.91]
axon --distinct_from--> dendrite [0.88]
axon --derived_from--> neuroblast [0.86]
```

### [action potential] — 5 valid
```
action potential --is_a--> electrical impulse [0.98]
action potential --part_of--> nerve conduction [0.95]
action potential --requires--> depolarization [0.97]
action potential --distinct_from--> synaptic transmission [0.99]
action potential --part_of--> neurotransmission [0.95]
```

### [myelin sheath] — 6 valid
```
myelin sheath --part_of--> peripheral nervous system [0.95]
myelin sheath --requires--> cholesterol [0.97]
myelin sheath --part_of--> central nervous system [0.94]
myelin sheath --contains--> galactocerebroside [0.93]
myelin sheath --distinct_from--> axon [0.92]
myelin sheath --part_of--> nervous tissue [0.91]
```

### [dopamine] — 6 valid
```
dopamine --part_of--> brain chemistry [0.98]
dopamine --requires--> tyrosine [0.95]
dopamine --enables--> motor control [0.92]
dopamine --enables--> reward processing [0.91]
dopamine --distinct_from--> serotonin [0.9]
dopamine --part_of--> catecholamines [0.89]
```

### [serotonin] — 6 valid
```
serotonin --part_of--> brain chemistry [0.95]
serotonin --contains--> tryptophan [0.9]
serotonin --requires--> 5-hydroxytryptophan [0.85]
serotonin --requires--> vitamin b6 [0.8]
serotonin --distinct_from--> dopamine [0.8]
serotonin --distinct_from--> adrenaline [0.85]
```

### [acetylcholine] — 4 valid
```
acetylcholine --part_of--> synapse [0.95]
acetylcholine --contains--> choline [0.98]
acetylcholine --derived_from--> tyrosine [0.95]
acetylcholine --distinct_from--> dopamine [0.98]
```

### [gaba] — 5 valid
```
gaba --part_of--> brain chemistry [0.95]
gaba --requires--> glutamate [0.96]
gaba --enables--> inhibitory postsynaptic potential [0.97]
gaba --used_for--> sedation [0.94]
gaba --distinct_from--> serotonin [0.95]
```

### [neuroplasticity] — 6 valid
```
neuroplasticity --is_a--> brain function [0.99]
neuroplasticity --part_of--> nervous system [0.98]
neuroplasticity --contains--> long-term potentiation [0.97]
neuroplasticity --requires--> neurotransmitters [0.96]
neuroplasticity --enables--> learning and memory [0.95]
neuroplasticity --distinct_from--> neuronal degeneration [0.92]
```

### [synaptic cleft] — 7 valid
```
synaptic cleft --part_of--> synapse [0.99]
synaptic cleft --contains--> neurotransmitters [0.97]
synaptic cleft --enables--> neurotransmission [0.95]
synaptic cleft --distinct_from--> postsynaptic density [0.93]
synaptic cleft --part_of--> neurotransmitter system [0.99]
synaptic cleft --contains--> acetylcholine [0.98]
synaptic cleft --enables--> excitation [0.92]
```

### [coronary artery] — 6 valid
```
coronary artery --requires--> blood flow [0.97]
coronary artery --part_of--> cardiovascular system [0.94]
coronary artery --distinct_from--> pulmonary artery [0.93]
coronary artery --contains--> epicardial fat [0.92]
coronary artery --requires--> nitric oxide [0.91]
coronary artery --part_of--> thoracic cavity [0.89]
```

### [atherosclerosis] — 6 valid
```
atherosclerosis --is_a--> vascular disease [0.99]
atherosclerosis --part_of--> cardiovascular system [0.98]
atherosclerosis --contains--> plaque [0.97]
atherosclerosis --requires--> inflammation [0.92]
atherosclerosis --derived_from--> endothelial dysfunction [0.93]
atherosclerosis --distinct_from--> arteriosclerosis [0.99]
```

### [myocardium] — 6 valid
```
myocardium --part_of--> heart [0.95]
myocardium --requires--> oxygen [0.92]
myocardium --requires--> nutrients [0.9]
myocardium --enables--> heart contraction [0.96]
myocardium --used_for--> blood circulation [0.94]
myocardium --distinct_from--> skeletal muscle [0.97]
```

### [systole] — 6 valid
```
systole --part_of--> heartbeat [0.95]
systole --part_of--> cardiac cycle [0.99]
systole --distinct_from--> diastole [0.98]
systole --is_a--> cardiovascular event [0.99]
systole --requires--> electrical impulse [0.95]
systole --enables--> pumping action [0.96]
```

### [diastole] — 2 valid
```
diastole --distinct_from--> systole [0.93]
diastole --part_of--> cardiac cycle [0.92]
```
Low yield. Pass 3b recovery candidate.

### [hypertension] — 8 valid
```
hypertension --is_a--> cardiovascular disorder [0.99]
hypertension --part_of--> blood pressure regulation [0.95]
hypertension --contains--> systolic blood pressure [0.98]
hypertension --requires--> renal function [0.97]
hypertension --enables--> cardiac remodeling [0.96]
systolic blood pressure --part_of--> blood pressure [0.98]
diastolic blood pressure --part_of--> blood pressure [0.97]
hypertension --distinct_from--> hypotension [0.99]
```

### [hypotension] — 5 valid (2 flagged)
```
hypotension --part_of--> cardiovascular system [0.95]
hypotension --contains--> low blood pressure [0.99]   ← REJECT: tautological
hypotension --distinct_from--> hypertension [0.99]
hypotension --part_of--> septic shock [0.92]
hypotension --contains--> vasodilation [0.91]   ← REJECT: wrong direction
```

### [angiogenesis] — 3 valid (1 flagged)
```
angiogenesis --is_a--> biological_process [0.99]   ← REJECT: underscore artefact; approve if anchor exists
angiogenesis --distinct_from--> apoptosis [0.93]
angiogenesis --contains--> pericytes [0.91]
```
Low yield. Pass 3b recovery candidate.

### [malignant tumor] — 8 valid
```
malignant tumor --part_of--> organ [0.95]
malignant tumor --contains--> cancer cells [0.98]
malignant tumor --requires--> angiogenesis [0.97]
malignant tumor --enables--> metastasis [0.96]
malignant tumor --used_for--> cancer diagnosis [0.95]
malignant tumor --derived_from--> genetic mutation [0.94]
malignant tumor --distinct_from--> benign tumor [0.93]
malignant tumor --part_of--> tissue [0.92]
```

### [tumor suppressor] — 15 valid (3 flagged)
```
tumor suppressor --is_a--> protein [0.99]
tumor suppressor --part_of--> cellular pathway [0.95]
tumor suppressor --contains--> p53 protein [0.98]
tumor suppressor --requires--> dna damage [0.97]
tumor suppressor --enables--> apoptosis [0.96]
tumor suppressor --used_for--> cancer prevention [0.99]
p53 protein --part_of--> tumor suppressor [0.98]
dna damage --requires--> uv radiation [0.95]   ← REJECT: wrong direction
apoptosis --enables--> cell death [0.99]
tumor suppressor --distinct_from--> oncogene [0.98]
cellular pathway --part_of--> cell signaling [0.96]
p53 protein --is_a--> transcription factor [0.97]
tumor suppressor --derived_from--> genetic mutation [0.95]
cancer prevention --used_for--> patient treatment [0.99]   ← REJECT: too abstract
cell death --requires--> mitochondrial dysfunction [0.98]   ← REJECT: wrong direction
```

### [hallucination] — 7 valid
```
hallucination --is_a--> perceptual experience [0.98]
hallucination --part_of--> psychosis [0.95]
hallucination --contains--> visual hallucination [0.97]
hallucination --requires--> abnormal brain activity [0.96]
hallucination --distinct_from--> illusion [0.99]
hallucination --part_of--> psychiatric disorder [0.98]
hallucination --contains--> auditory hallucination [0.96]
```

### [delusion] — 6 valid (2 flagged)
```
delusion --part_of--> psychosis [0.95]
delusion --contains--> false belief [0.98]
delusion --requires--> cognitive distortion [0.97]
delusion --used_for--> avoidance behavior [0.95]   ← REJECT: wrong use of used_for
delusion --distinct_from--> hallucination [0.99]
delusion --enables--> social withdrawal [0.96]   ← BORDERLINE: consequence, not enabling
```

### [ptsd] — 3 valid
```
post-traumatic stress disorder --is_a--> mental disorder [0.99]
post-traumatic stress disorder --contains--> flashbacks [0.9]
post-traumatic stress disorder --enables--> hypervigilance [0.96]
```
Note: subject resolved as "post-traumatic stress disorder" not "ptsd" — check anchor match.

### [cognitive behavioral therapy] — 5 valid
```
cognitive behavioral therapy --is_a--> psychotherapy [0.99]
cognitive behavioral therapy --enables--> emotional regulation [0.96]
cognitive behavioral therapy --derived_from--> behavioral psychology [0.94]
cognitive behavioral therapy --distinct_from--> pharmacotherapy [0.93]
cognitive behavioral therapy --contains--> cognitive restructuring [0.91]
```

### [major depressive disorder] — 7 valid
```
major depressive disorder --is_a--> mental health disorder [0.99]
major depressive disorder --part_of--> mood disorders [0.95]
major depressive disorder --requires--> neurotransmitter regulation [0.97]
major depressive disorder --enables--> anhedonia [0.94]
major depressive disorder --distinct_from--> bipolar disorder [0.93]
major depressive disorder --part_of--> psychiatric disorders [0.92]
neurotransmitter regulation --requires--> brain regions [0.97]
```

### [antipsychotic] — 8 valid
```
antipsychotic --is_a--> medication [0.99]
antipsychotic --part_of--> psychopharmacology [0.95]
antipsychotic --requires--> prescription [0.97]
antipsychotic --enables--> symptom relief [0.96]
antipsychotic --derived_from--> chemical synthesis [0.95]   ← REJECT: process
antipsychotic --distinct_from--> anxiolytic [0.98]
antipsychotic --is_a--> neuroleptic [0.99]
antipsychotic --part_of--> psychiatric treatment [0.97]
```

---

## Summary for GPT

**566 proposed. Pre-flagged rejects (~35 relations):**

Direction errors: expiration→lung, mucus→bronchus, cilia→bronchus, vein→limb, vein→organ, atrium→endocardium, atrium→myocardium, ischemia contains MI, infarction part_of stroke, infarction derived_from angiography/CT, t cell is_a effector t cell, immunoglobulin part_of antibody, dna damage requires uv radiation, cell death requires mitochondrial dysfunction, antiviral requires viral replication, hypotension contains vasodilation

Process-as-object: mitochondria→endosymbiosis, glucose→photosynthesis, allele→natural selection, lymphoma→genetic mutations, antidepressant→chemical synthesis, antipsychotic→chemical synthesis

Tautological/confused: adrenaline contains epinephrine/norepinephrine, hypotension contains low blood pressure, half-life is_a biological_process, lymphoma used_for diagnosis and treatment, adenoma used_for endocrine function, delusion used_for avoidance behavior, cancer prevention used_for patient treatment, glucose contains carbon/hydrogen atoms

**Pass 3b recovery candidates (very low yield):**
aorta (0), transcription (1), interleukin (1), diastole (2), interferon (2), atp (3), angiogenesis (3)

**Instructions:** Approve or override each flagged reject. Add any additional rejects you spot. For Pass 3b candidates, advise whether to manual-seed or skip.
