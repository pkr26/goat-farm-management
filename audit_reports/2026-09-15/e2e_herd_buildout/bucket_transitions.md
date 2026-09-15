# Bucket movement analysis — transition matrices

Every move the daily engine executed, grouped by (from, to, workflow context). The engine validates each move against the same legal bucket graph the live app enforces (`models.lifecycle.LEGAL_BUCKET_TRANSITIONS`).

## What each bucket means

| Bucket | Meaning |
|---|---|
| QUARANTINE | New arrivals isolated for the 45-day protocol (inspection, deworming, PPR/ET+T/Goat Pox/FMD vaccines, fecal checks, footbath). |
| FOUNDATION | Cleared arrivals and growing stock — the grow-out pen where young does wait to reach the 12-month breeding gate. |
| BREEDING | Open does cycling and being served; the bucks' pen. |
| PREGNANCY_EARLY | Scan-confirmed does, gestation day 32-99. |
| PREGNANCY_LATE | Gestating does from day 100; pre-kidding ET+TT vaccines fire here (due-date −40 and −25). |
| DELIVERY | Kidding pen; does move in 15 days before the due date. |
| RECOVERY | Fresh dams with their unweaned kids (kids on creep feed); weaning at day 60 moves kids out. |
| RESTING | Post-weaning dry-off and flush (~30 days) before the doe returns to BREEDING. |
| MALE_KIDS | Weaned males growing to the 8-9 month meat window. |
| FEMALE_KIDS | Weaned females growing toward the breeding gate. |

## Batch 1 — arrival year (365 days from 2026-10-01)

| From | To | Context | Moves |
|---|---|---|---:|
| BREEDING | PREGNANCY_EARLY | ultrasound | 48 |
| DELIVERY | RECOVERY | kidding | 37 |
| FOUNDATION | BREEDING | breeding | 51 |
| PREGNANCY_EARLY | PREGNANCY_LATE | manual | 47 |
| PREGNANCY_LATE | DELIVERY | delivery | 37 |
| PREGNANCY_LATE | RESTING | abortion | 2 |
| QUARANTINE | FOUNDATION | quarantine_release | 52 |
| RECOVERY | RESTING | postpartum | 1 |
| RESTING | BREEDING | breeding | 2 |


## Continuation — kidding → re-breeding (from 2027-08-01)

| From | To | Context | Moves |
|---|---|---|---:|
| BREEDING | PREGNANCY_EARLY | ultrasound | 10 |
| DELIVERY | RECOVERY | kidding | 18 |
| PREGNANCY_EARLY | DELIVERY | delivery | 9 |
| PREGNANCY_LATE | DELIVERY | delivery | 10 |
| RECOVERY | FEMALE_KIDS | weaning | 13 |
| RECOVERY | MALE_KIDS | weaning | 11 |
| RECOVERY | RESTING | postpartum | 1 |
| RECOVERY | RESTING | weaning | 17 |
| RESTING | BREEDING | breeding | 18 |


## Whole-herd snapshot — 90 days from 2028-04-01

| From | To | Context | Moves |
|---|---|---|---:|
| BREEDING | PREGNANCY_EARLY | ultrasound | 341 |
| FOUNDATION | BREEDING | breeding | 50 |
| PREGNANCY_EARLY | RESTING | abortion | 4 |
| RESTING | BREEDING | breeding | 3 |
