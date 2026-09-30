# Baseline updates

Each entry says when the stored baseline (`tests/baseline/`) was rewritten, why, and how the new run compared with the one it replaced (`tools/reference/check_reference.py --update-baseline`).

## 2026-09-27 — S, merge

- Reason: The first stored run of this tree: noun-phrase candidates, the trilingual demo vocabulary and the triage without a catalogue anchor.
- Engine: 1.0.0.dev0, source fingerprint `d6f60378cbaf488d`
- S: the first baseline
- merge: the first baseline

## 2026-09-27 — L

- Reason: The first stored run of this tree: noun-phrase candidates, the trilingual demo vocabulary and the triage without a catalogue anchor.
- Engine: 1.0.0.dev0, source fingerprint `d6f60378cbaf488d`
- L: the first baseline

## 2026-09-28 — S, merge

- Reason: The extraction's raw tables gain the columns people, texts, band and reason (and the merged list band and reason), and the settings snapshot records the counting unit; every score is unchanged.
- Engine: 1.0.0.dev0, source fingerprint `faa3c93a5ed70924`
- S: against the previous baseline, 12 stages: 10 identical, 2 different (extract, build)
- merge: against the previous baseline, 1 stage: 1 identical

## 2026-09-28 — L

- Reason: The extraction's raw tables gain the columns people, texts, band and reason (and the merged list band and reason), and the settings snapshot records the counting unit; every score is unchanged.
- Engine: 1.0.0.dev0, source fingerprint `faa3c93a5ed70924`
- L: against the previous baseline, 12 stages: 10 identical, 2 different (extract, build)

## 2026-09-28 — S, merge

- Reason: The lexicon lab's defaults: English noun phrases take no of complement, nothing is set aside for a low score, and a candidate is a fragment of a longer one only when it is never seen outside it. The English candidates change, and with them every later stage; the French candidates change only in their band and reason.
- Engine: 1.0.0.dev0, source fingerprint `b9a57a10e58fae0a`
- S: against the previous baseline, 12 stages: 1 identical, 11 different (extract, triage, build, space, group, layout, draft, apply, trajectories, projection, bundle)
- merge: against the previous baseline, 1 stage: 0 identical, 1 different (merge)

## 2026-09-28 — L

- Reason: The lexicon lab's defaults: English noun phrases take no of complement, nothing is set aside for a low score, and a candidate is a fragment of a longer one only when it is never seen outside it. The English candidates change, and with them every later stage; the French candidates change only in their band and reason.
- Engine: 1.0.0.dev0, source fingerprint `b9a57a10e58fae0a`
- L: against the previous baseline, 12 stages: 1 identical, 11 different (extract, triage, build, space, group, layout, draft, apply, trajectories, projection, bundle)

## 2026-09-28 — S, merge

- Reason: Points are placed on the finished map by their nearest mapped people instead of UMAP's transform (gate G2): each keyword, trajectory point and window, and projected person goes to the weighted mean of the heaviest linked group of its 8 nearest people in the SVD space (link radius a quarter of the map's radius). The people's own positions are unchanged; the layout's keyword positions and diagnostics, the trajectories and the projection move.
- Engine: 1.0.0.dev0, source fingerprint `0461d2e57d55b3aa`
- S: against the previous baseline, 12 stages: 9 identical, 3 different (layout, trajectories, projection)
- merge: against the previous baseline, 1 stage: 1 identical

## 2026-09-28 — L

- Reason: Points are placed on the finished map by their nearest mapped people instead of UMAP's transform (gate G2): each keyword, trajectory point and window, and projected person goes to the weighted mean of the heaviest linked group of its 8 nearest people in the SVD space (link radius a quarter of the map's radius). The people's own positions are unchanged; the layout's keyword positions and diagnostics, the trajectories and the projection move.
- Engine: 1.0.0.dev0, source fingerprint `0461d2e57d55b3aa`
- L: against the previous baseline, 12 stages: 9 identical, 3 different (layout, trajectories, projection)

## 2026-09-28 — S, merge

- Reason: The owner's lexicon decisions (gate G2): the common-modifier band rule is off, so phrases with a widespread edge adjective are kept instead of to check; the AI clean-up judges the kept and to-check bands only, never the set-aside band, with the third version of its prompt. The raw tables change in band and reason only; the triage sees fewer candidates, and every later stage follows.
- Engine: 1.0.0.dev0, source fingerprint `a6b20ae5783613e1`
- S: against the previous baseline, 12 stages: 1 identical, 6 within tolerance, 5 different (build, space, layout, trajectories, projection)
- merge: against the previous baseline, 1 stage: 0 identical, 1 within tolerance

## 2026-09-28 — L

- Reason: The owner's lexicon decisions (gate G2): the common-modifier band rule is off, so phrases with a widespread edge adjective are kept instead of to check; the AI clean-up judges the kept and to-check bands only, never the set-aside band, with the third version of its prompt. The raw tables change in band and reason only; the triage sees fewer candidates, and every later stage follows.
- Engine: 1.0.0.dev0, source fingerprint `a6b20ae5783613e1`
- L: against the previous baseline, 12 stages: 1 identical, 6 within tolerance, 5 different (build, space, layout, trajectories, projection)

## 2026-09-29 — S, merge

- Reason: Theme names in each language: every node of the draft is named, in each language, after its most used keyword that has a form there (its own language, or one the consolidation pairs attest), else after the reference-language form of the most used keyword that has one. The demo vocabulary is in English (the AI clean-up's canonical forms), so the English labels do not move; the French labels of the concepts and subfields do (a subfield's French label no longer repeats its English one), and the applied document follows. Nothing else changes.
- Engine: 1.0.0.dev0, source fingerprint `5bc3ecae79036f6c`
- S: against the previous baseline, 12 stages: 10 identical, 2 different (draft, apply)
- merge: against the previous baseline, 1 stage: 1 identical

## 2026-09-29 — L

- Reason: Theme names in each language: every node of the draft is named, in each language, after its most used keyword that has a form there (its own language, or one the consolidation pairs attest), else after the reference-language form of the most used keyword that has one. The demo vocabulary is in English (the AI clean-up's canonical forms), so the English labels do not move; the French labels of the concepts and subfields do (a subfield's French label no longer repeats its English one), and the applied document follows. Nothing else changes.
- Engine: 1.0.0.dev0, source fingerprint `5bc3ecae79036f6c`
- L: against the previous baseline, 12 stages: 10 identical, 2 different (draft, apply)

## 2026-09-29 — S, merge

- Reason: Stop words and evenly spread single words are set aside: a single word among the language's spaCy stop words or closed words, a closed word of another language in a paragraph read as that language (two different ones among its phrases; there they also cut phrases), a phrase starting or ending with another language's closed word, and a single word used by at least a fifth of the people as evenly as a randomly scattered word. The raw tables change in band and reason, and the English candidates of paragraphs read as French change; the triage sees fewer candidates, and every later stage follows.
- Engine: 1.0.0.dev0, source fingerprint `e2a5bef153603723`
- S: against the previous baseline, 12 stages: 10 identical, 2 different (extract, triage)
- merge: against the previous baseline, 1 stage: 1 identical

## 2026-09-29 — L

- Reason: Stop words and evenly spread single words are set aside: a single word among the language's spaCy stop words or closed words, a closed word of another language in a paragraph read as that language (two different ones among its phrases; there they also cut phrases), a phrase starting or ending with another language's closed word, and a single word used by at least a fifth of the people as evenly as a randomly scattered word. The raw tables change in band and reason, and the English candidates of paragraphs read as French change; the triage sees fewer candidates, and every later stage follows.
- Engine: 1.0.0.dev0, source fingerprint `e2a5bef153603723`
- L: against the previous baseline, 12 stages: 10 identical, 2 different (extract, triage)

## 2026-09-29 — S, merge

- Reason: Keyword categories and the rejection lists: the AI triage judges every band but the rejected one (the set-aside band too, so a rule's set-aside can be rescued), with the codes P, D and H added and a category stored in each per-term answer. The extraction's tables are unchanged (the shipped rejection lists are empty and the reference run has no machine cache); the triage sees more candidates and accepts some set-aside ones, and every later stage follows; a theme's name prefers a concept or an object on a tie of use.
- Engine: 1.0.0.dev0, source fingerprint `c1107b902d974b0a`
- S: against the previous baseline, 12 stages: 2 identical, 10 different (triage, build, space, group, layout, draft, apply, trajectories, projection, bundle)
- merge: against the previous baseline, 1 stage: 0 identical, 1 different (merge)

## 2026-09-29 — L

- Reason: Keyword categories and the rejection lists (step 7): the AI judges every band but the rejected one, candidates on the rejection lists go to a new rejected band, keywords.build writes categories.json and theme names prefer a concept or an object on a tie.
- Engine: 1.0.0.dev0, source fingerprint `c1107b902d974b0a`
- L: against the previous baseline, 12 stages: 2 identical, 10 different (triage, build, space, group, layout, draft, apply, trajectories, projection, bundle)

## 2026-09-29 — S, merge

- Reason: Candidates from three texts, a work read once: a keyword candidate must also occur in at least 3 distinct texts (keywords.extract.min_texts, default 3), so phrases of one or two co-authored texts leave the extraction and every later stage follows; the S world then keeps fewer keywords than the default 150 topics, so the reference groups them into one topic fewer than its keywords (the same in both modes; L keeps 150); corpus.assemble reads one text per work (no duplicate in the demo worlds, so the corpus is unchanged).
- Engine: 1.0.0.dev0, source fingerprint `e28138d8ba9e6056`
- S: against the previous baseline, 12 stages: 1 identical, 11 different (extract, triage, build, space, group, layout, draft, apply, trajectories, projection, bundle)
- merge: against the previous baseline, 1 stage: 0 identical, 1 different (merge)

## 2026-09-29 — L

- Reason: Candidates from three texts, a work read once: a keyword candidate must also occur in at least 3 distinct texts (keywords.extract.min_texts, default 3), so phrases of one or two co-authored texts leave the extraction and every later stage follows; the S world then keeps fewer keywords than the default 150 topics, so the reference groups them into one topic fewer than its keywords (the same in both modes; L keeps 150); corpus.assemble reads one text per work (no duplicate in the demo worlds, so the corpus is unchanged).
- Engine: 1.0.0.dev0, source fingerprint `e28138d8ba9e6056`
- L: against the previous baseline, 12 stages: 1 identical, 11 different (extract, triage, build, space, group, layout, draft, apply, trajectories, projection, bundle)
