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
