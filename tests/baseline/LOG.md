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
