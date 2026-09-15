# Server-generated task title keys (backend-core workstream, item 16)

Every server-generated duty now carries `title_key` (stable snake_case) and
`title_args` (JSONB) alongside the English `title` fallback. Both fields are
exposed on `TaskOut`. Manual duties have `title_key: null` and
`title_args: {}`. The frontend i18n catalog is built from this list; arg
values marked `date` are ISO `YYYY-MM-DD` strings.

## Breeding / ultrasound (services/breeding.py)

| key | args |
|---|---|
| `pregnancy_check` | `tag`, `breeding_date` (date), `due_date` (date) |
| `return_to_heat_watch` | `tag`, `due_date` (date) |
| `pre_kidding_vaccine` | `tag`, `due_date` (date) |
| `pre_kidding_vaccine_booster` | `tag`, `due_date` (date) |
| `move_to_delivery` | `tag`, `kidding_date` (date), `due_date` (date) |
| `move_to_pregnancy_late` | `tag`, `due_date` (date) |
| `birthing_kit_check` | `tag`, `kidding_date` (date), `due_date` (date) |
| `kidding_watch` | `tag`, `kidding_date` (date), `days_before` (int, 0 = due-day labor watch), `due_date` (date) |
| `kidding_due` | `tag`, `due_date` (date) |

## Kidding / postpartum (services/kidding.py)

| key | args |
|---|---|
| `wean_kids` | `tag`, `due_date` (date) |
| `move_to_resting` | `tag`, `due_date` (date) |
| `post_kidding_dam_check` | `tag`, `due_date` (date) |
| `kidding_stall_cleanout` | `tag`, `due_date` (date) |
| `kid_support` | `tag`, `due_date` (date) |

## Task service (services/tasks.py)

| key | args |
|---|---|
| `rebreed` | `tag`, `due_date` (date) |

## Finance (services/finance.py)

| key | args |
|---|---|
| `insurance_renewal` | `policy_number`, `renewal_date` (date), `due_date` (date) |

## Cadence sweep (services/cadence.py)

| key | args |
|---|---|
| `fmd_vaccination_round` | `month` (English name), `year` (int), `due_date` (date) |
| `et_hs_premonsoon_round` | `month`, `year`, `due_date` (date) |
| `goat_pox_round` | `month`, `year`, `due_date` (date) |
| `ccpp_round` | `month`, `year`, `due_date` (date) |
| `deworming_round` | `month`, `year`, `due_date` (date) |
| `hoof_trimming_round` | `due_date` (date) |
| `ectoparasite_spray_round` | `due_date` (date) |
| `shed_disinfection_round` | `due_date` (date) |
| `monthly_weighing_round` | `due_date` (date) |
| `morning_feed_routine` | `due_date` (date) |
| `daily_water_check` | `due_date` (date) |
| `feed_reorder` | `ingredient`, `qty_on_hand` (float kg), `reorder_level` (float kg), `due_date` (date) |
| `buck_rotation` | `tag`, `age_months` (int), `due_date` (date) |

## Quarantine protocol (models/helpers.py `quarantine_schedule`)

All carry `batch_id` (int), `day` (int protocol day), `due_date` (date).

| key | protocol day |
|---|---|
| `quarantine_arrival_inspection` | 1 |
| `quarantine_rest` | 1 |
| `quarantine_deworm` | 4 |
| `quarantine_liver_tonic` | 5 |
| `quarantine_ppr_vaccine` | 10 |
| `quarantine_fecal_exam` | 13 |
| `quarantine_et_tetanus_vaccine` | 20 |
| `quarantine_goat_pox_vaccine` | 30 |
| `quarantine_prerelease_review` | 30 |
| `quarantine_fmd_vaccine` | 40 |
| `quarantine_release` | 45 |

## Dashboard advisories (not tasks, same key/args contract)

`DashboardOut.advisory` is `{"key": ..., "args": {...}} | null`.

| key | args |
|---|---|
| `bakrid_hold` | `count` (int), `festival_date` (date) |

Total: 40 task title keys + 1 dashboard advisory key.
