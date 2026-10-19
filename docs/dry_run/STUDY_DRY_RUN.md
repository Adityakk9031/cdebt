# `cdebt study` dry run (PIPELINE TEST, SYNTHETIC DATA)

**Everything below is fake.** The repository is generated, the 'participants' are a random number generator with accuracy rates chosen by the script, and the numbers prove nothing about people or about cdebt. This only shows that `init`, `run`, `analyze` and `power` work end to end and that the privacy properties hold. Regenerate with `uv run python scripts/study_dry_run.py`.

## 1. `study init`

```
wrote <tmp>/study.json (10 questions over 5 files, commit 9c611aa824) and <tmp>/study.secret.json. Send ONLY <tmp>/study.json to participants; keep <tmp>/study.secret.json private.
```

## 2. Only 3 fake participants (below the minimum of 5)

```
study 71409650685e68d6: 3 participants, 1 team label(s), 30 responses (cells 
with < 5 participants are suppressed)
                    accuracy by self-reported familiarity                     
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ familiarity ┃ correct/n  ┃ rate ┃ 95% CI (responses, naive) ┃ participants ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ wrote       │ suppressed │      │                           │ 3            │
│ reviewed    │ suppressed │      │                           │ 3            │
│ never       │ suppressed │      │                           │ 3            │
└─────────────┴────────────┴──────┴───────────────────────────┴──────────────┘
Pre-registered contrast, familiar (wrote|reviewed) minus never saw:
{'difference': None, 'ci95': None, 'note': 'too few participants'}
Same, excluding consulted responses:
{'difference': None, 'ci95': None, 'note': 'too few participants'}
only 3 participants (< 5): cells are suppressed; this output is not 
interpretable
2 of 30 responses were self-reported as consulted (looked up / asked); see the 
sensitivity analysis
```

Every cell is suppressed and no contrast is given.

## 3. 12 fake participants (8 in team A, 4 in team B)

Planted by the script: accuracy 0.85 if familiar, 0.45 if never saw. The point is only that the pipeline recovers a planted difference; it says nothing real.

```json
{
 "participants": 12,
 "teams": 2,
 "responses": 120,
 "by_familiarity": {
  "wrote": {
   "correct": 35,
   "n": 37,
   "rate": 0.9459459459459459,
   "ci95": [
    0.822953455158113,
    0.9850490233016582
   ],
   "participants": 12,
   "suppressed": false
  },
  "reviewed": {
   "correct": 31,
   "n": 35,
   "rate": 0.8857142857142857,
   "ci95": [
    0.7404853121378726,
    0.9546482069844427
   ],
   "participants": 12,
   "suppressed": false
  },
  "never": {
   "correct": 16,
   "n": 48,
   "rate": 0.3333333333333333,
   "ci95": [
    0.21676780617841943,
    0.4745989040710741
   ],
   "participants": 12,
   "suppressed": false
  }
 },
 "familiar_vs_never": {
  "difference": 0.5833333333333333,
  "familiar_n": 72,
  "never_n": 48,
  "ci95": [
   0.42647058823529416,
   0.7111111111111111
  ],
  "bootstrap": "participants resampled, 500 replicates, seed 0",
  "hypothesis_supported": true
 },
 "by_team": {
  "FAKE-team-A": {
   "correct": 54,
   "n": 80,
   "rate": 0.675,
   "ci95": [
    0.5664052722236533,
    0.7675583821975184
   ],
   "participants": 8,
   "suppressed": false
  },
  "FAKE-team-B": {
   "correct": null,
   "n": null,
   "rate": null,
   "ci95": null,
   "participants": 4,
   "suppressed": true
  }
 },
 "notes": [
  "8 of 120 responses were self-reported as consulted (looked up / asked); see the sensitivity analysis"
 ]
}
```

Team B has 4 participants (< 5), so its cell is suppressed.

## 4. `study power`

```
design effect 2.10; 271 responses per group; about 46 participants if every participant answers 12 questions split evenly between familiar and never-saw code (a planning estimate, not a guarantee)
```

## 5. Privacy and leak checks (asserted by the script)

| check | result |
|---|---|
| study file contains no HMAC key and no answer MACs | PASS |
| study file has no 'answer' field per question | PASS |
| secret file is a separate file | PASS |
| 12 response files written | PASS |
| participant ids are random 8-hex | PASS |
| response files hold no name/host/time fields | PASS |
| no participant id appears in any analysis output | PASS |
| 3-participant analysis suppressed every cell | PASS |
| team B (4 participants) is suppressed | PASS |
| team A (8 participants) is shown | PASS |
| the planted effect is recovered by the pipeline (synthetic) | PASS |

These are pipeline checks. Do not cite any number on this page as evidence.
