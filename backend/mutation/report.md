# Backend mutation testing report

- mutants in manifest: **6730**
- executed: **6730** (killed 4781, timeout 106, survived 1337, not-covered 506, errors 0)
- **mutation score: 78.5%** (killed / executed-with-coverage)

## Surviving mutants

### `app/api/_run_limits.py` (8 survivors)

- **L126** `intconst` n -> n+1 — `75abeef94a90`
  - `_RUN_BUDGET_WINDOW_SECONDS = 300`
  - `_RUN_BUDGET_WINDOW_SECONDS = 301`
- **L128** `intconst` n -> n-1 — `a4d1946128bd`
  - `_RUN_BUDGET_MAX_KEYS = 50000`
  - `_RUN_BUDGET_MAX_KEYS = 49999`
- **L134** `intconst` n -> n+1 — `8d74ba5b8d32`
  - `_BUSY_RETRY_AFTER_SECONDS = 5`
  - `_BUSY_RETRY_AFTER_SECONDS = 6`
- **L134** `intconst` n -> n-1 — `9222e76ae06f`
  - `_BUSY_RETRY_AFTER_SECONDS = 5`
  - `_BUSY_RETRY_AFTER_SECONDS = 4`
- **L203** `boolconst` True -> False — `a1490b665d46`
  - `self._prune_bucket(bucket, touch=True)`
  - `self._prune_bucket(bucket, touch=False)`
- **L216** `boolconst` True -> False — `ca2f94f59d1b`
  - `self._reclassify(bucket, touch=True)`
  - `self._reclassify(bucket, touch=False)`
- **L282** `boolconst` False -> True — `48598c5bfacc`
  - `self._prune_bucket(candidate, touch=False)`
  - `self._prune_bucket(candidate, touch=True)`
- **L363** `boolconst` True -> False — `2ea2b4a4ed7a`
  - `released = True`
  - `released = False`

### `app/api/_shared.py` (4 survivors)

- **L252** `boolconst` False -> True — `b67ccb71b47a`
  - `out.necropsy_done = False`
  - `out.necropsy_done = True`
- **L293** `loopjump` break -> continue — `1d88a7972c81`
  - `break`
  - `continue`
- **L419** `boolconst` True -> False — `ebe6901062c3`
  - `assignee_membership_statement = assignee_membership_statement.with_for_update(read=True)`
  - `assignee_membership_statement = assignee_membership_statement.with_for_update(read=False)`
- **L423** `boolconst` True -> False — `65cbdf94bfbf`
  - `assignee_user_statement = assignee_user_statement.with_for_update(read=True)`
  - `assignee_user_statement = assignee_user_statement.with_for_update(read=False)`

### `app/api/animals.py` (14 survivors)

- **L331** `boolop` Or -> And — `00deb3ecb5f6`
  - `recorded_dob = payload.date_of_birth or payload.estimated_dob`
  - `recorded_dob = payload.date_of_birth and payload.estimated_dob`
- **L394** `boolop` Or -> And — `87e9df39a3ff`
  - `books_money = managed_purchase or (payload.source == 'PURCHASED' and payload.purchase_price is not None)`
  - `books_money = managed_purchase and (payload.source == 'PURCHASED' and payload.purchase_price is not None)`
- **L442** `binop` Sub -> Add — `5093992fb90c`
  - `age_months = (farm_date.year - dob.year) * 12 + (farm_date.month - dob.month)`
  - `age_months = (farm_date.year - dob.year) * 12 + (farm_date.month + dob.month)`
- **L442** `binop` Sub -> Add — `0760d53cce60`
  - `age_months = (farm_date.year - dob.year) * 12 + (farm_date.month - dob.month)`
  - `age_months = (farm_date.year + dob.year) * 12 + (farm_date.month - dob.month)`
- **L444** `intconst` n -> n+1 — `6a73f9c9238b`
  - `age_months -= 1`
  - `age_months -= 2`
- **L444** `intconst` n -> n-1 — `0d3fdaac2dc7`
  - `age_months -= 1`
  - `age_months -= 0`
- **L478** `intconst` n -> n+1 — `26d01a2dd225`
  - `raise HTTPException(status_code=422, detail=f'{weight_profile.young} birth weight must be between {weight_profile.birth_weight_kg_range[0]:g} and {weight_profile.birth_weight_kg_range[1]:g} kg — {payload.birth_weight:g} kg is not a credible newborn weight')`
  - `raise HTTPException(status_code=422, detail=f'{weight_profile.young} birth weight must be between {weight_profile.birth_weight_kg_range[1]:g} and {weight_profile.birth_weight_kg_range[1]:g} kg — {payload.birth_weight:g} kg is not a credible newborn weight')`
- **L479** `intconst` n -> n-1 — `db8cfe7156a1`
  - `raise HTTPException(status_code=422, detail=f'{weight_profile.young} birth weight must be between {weight_profile.birth_weight_kg_range[0]:g} and {weight_profile.birth_weight_kg_range[1]:g} kg — {payload.birth_weight:g} kg is not a credible newborn weight')`
  - `raise HTTPException(status_code=422, detail=f'{weight_profile.young} birth weight must be between {weight_profile.birth_weight_kg_range[0]:g} and {weight_profile.birth_weight_kg_range[0]:g} kg — {payload.birth_weight:g} kg is not a credible newborn weight')`
- **L574** `intconst` n -> n-1 — `6523ad1c357d`
  - `db.add(BucketMove(animal_id=animal.id, from_bucket=None, to_bucket=initial_bucket, effective_date=animal.purchase_date or animal.date_of_birth or animal.estimated_dob or farm_date, reason=f'Historical import: {historical_import_reason}'[:255] if historical_import_reason else f'Purchase batch #{purchase_batch.id}' if purchase_batch is not None else 'Initial entry', created_by_id=user_id))`
  - `db.add(BucketMove(animal_id=animal.id, from_bucket=None, to_bucket=initial_bucket, effective_date=animal.purchase_date or animal.date_of_birth or animal.estimated_dob or farm_date, reason=f'Historical import: {historical_import_reason}'[:254] if historical_import_reason else f'Purchase batch #{purchase_batch.id}' if purchase_batch is not None else 'Initial entry', created_by_id=user_id))`
- **L577** `ifexp` swap branches — `e80bc05b896a`
  - `db.add(BucketMove(animal_id=animal.id, from_bucket=None, to_bucket=initial_bucket, effective_date=animal.purchase_date or animal.date_of_birth or animal.estimated_dob or farm_date, reason=f'Historical import: {historical_import_reason}'[:255] if historical_import_reason else f'Purchase batch #{purchase_batch.id}' if purchase_batch is not None else 'Initial entry', created_by_id=user_id))`
  - `db.add(BucketMove(animal_id=animal.id, from_bucket=None, to_bucket=initial_bucket, effective_date=animal.purchase_date or animal.date_of_birth or animal.estimated_dob or farm_date, reason=f'Historical import: {historical_import_reason}'[:255] if historical_import_reason else 'Initial entry' if purchase_batch is not None else f'Purchase batch #{purchase_batch.id}', created_by_id=user_id))`
- **L578** `compare` IsNot -> Is — `cff2336d3c01`
  - `db.add(BucketMove(animal_id=animal.id, from_bucket=None, to_bucket=initial_bucket, effective_date=animal.purchase_date or animal.date_of_birth or animal.estimated_dob or farm_date, reason=f'Historical import: {historical_import_reason}'[:255] if historical_import_reason else f'Purchase batch #{purchase_batch.id}' if purchase_batch is not None else 'Initial entry', created_by_id=user_id))`
  - `db.add(BucketMove(animal_id=animal.id, from_bucket=None, to_bucket=initial_bucket, effective_date=animal.purchase_date or animal.date_of_birth or animal.estimated_dob or farm_date, reason=f'Historical import: {historical_import_reason}'[:255] if historical_import_reason else f'Purchase batch #{purchase_batch.id}' if purchase_batch is None else 'Initial entry', created_by_id=user_id))`
- **L607** `ifexp` swap branches — `62390768c12c`
  - `db.add(Transaction(farm_id=farm_id, date=payload.purchase_date or farm_date, type=TransactionType.EXPENSE.value, category=TransactionCategory.ANIMAL_PURCHASE.value, amount=money(payload.purchase_price), related_animal_id=animal.id, notes=f'Purchase of {animal.tag_number}' + (f' from {animal.seller_name}' if animal.seller_name else ''), created_by_id=user_id, source_type='ANIMAL_PURCHASE', source_id=animal.id))`
  - `db.add(Transaction(farm_id=farm_id, date=payload.purchase_date or farm_date, type=TransactionType.EXPENSE.value, category=TransactionCategory.ANIMAL_PURCHASE.value, amount=money(payload.purchase_price), related_animal_id=animal.id, notes=f'Purchase of {animal.tag_number}' + ('' if animal.seller_name else f' from {animal.seller_name}'), created_by_id=user_id, source_type='ANIMAL_PURCHASE', source_id=animal.id))`
- **L1213** `intconst` n -> n+1 — `d7977c94f414`
  - `raise HTTPException(status_code=422, detail=f'{animal.tag_number} is {age_months} months old — the meat-sale window opens at {MEAT_SALE_AGE_MONTHS[0]} months and {MEAT_SALE_WEIGHT_KG[0]:.0f} kg (record a cull instead if the animal must leave the herd now)')`
  - `raise HTTPException(status_code=422, detail=f'{animal.tag_number} is {age_months} months old — the meat-sale window opens at {MEAT_SALE_AGE_MONTHS[1]} months and {MEAT_SALE_WEIGHT_KG[0]:.0f} kg (record a cull instead if the animal must leave the herd now)')`
- **L1309** `loopjump` continue -> break — `28df1ec07c03`
  - `continue`
  - `break`

### `app/api/auth.py` (23 survivors)

- **L223** `intconst` n -> n+1 — `185ff595ba57`
  - `default_port = {'http': 80, 'https': 443}[scheme]`
  - `default_port = {'http': 81, 'https': 443}[scheme]`
- **L223** `intconst` n -> n-1 — `d5811c2a777d`
  - `default_port = {'http': 80, 'https': 443}[scheme]`
  - `default_port = {'http': 79, 'https': 443}[scheme]`
- **L223** `intconst` n -> n+1 — `8ae6e2f0844d`
  - `default_port = {'http': 80, 'https': 443}[scheme]`
  - `default_port = {'http': 80, 'https': 444}[scheme]`
- **L223** `intconst` n -> n-1 — `e25b7fc1f1c2`
  - `default_port = {'http': 80, 'https': 443}[scheme]`
  - `default_port = {'http': 80, 'https': 442}[scheme]`
- **L418** `intconst` n -> n+1 — `3e1b4cce12fc`
  - `media_type = content_type.split(';', 2)[0].strip().lower()`
  - `media_type = content_type.split(';', 3)[0].strip().lower()`
- **L418** `intconst` n -> n-1 — `fc9cd5380316`
  - `media_type = content_type.split(';', 2)[0].strip().lower()`
  - `media_type = content_type.split(';', 1)[0].strip().lower()`
- **L502** `compare` Is -> IsNot — `b9cf956ea5e2`
  - `response.set_cookie(s.refresh_cookie_name, token, max_age=s.refresh_token_ttl_seconds if max_age is None else max_age, httponly=True, samesite='lax', secure=s.cookie_secure, path='/')`
  - `response.set_cookie(s.refresh_cookie_name, token, max_age=s.refresh_token_ttl_seconds if max_age is not None else max_age, httponly=True, samesite='lax', secure=s.cookie_secure, path='/')`
- **L503** `boolconst` True -> False — `fcf183bf33e5`
  - `response.set_cookie(s.refresh_cookie_name, token, max_age=s.refresh_token_ttl_seconds if max_age is None else max_age, httponly=True, samesite='lax', secure=s.cookie_secure, path='/')`
  - `response.set_cookie(s.refresh_cookie_name, token, max_age=s.refresh_token_ttl_seconds if max_age is None else max_age, httponly=False, samesite='lax', secure=s.cookie_secure, path='/')`
- **L1159** `binop` Mult -> Div — `bc6278283ebd`
  - `auth_limiter.record(WORKER_PIN_SPRAY_SCOPE, spray_key, s.worker_pin_rate_limit_window_seconds, max_attempts=10 * s.worker_pin_rate_limit_max_attempts)`
  - `auth_limiter.record(WORKER_PIN_SPRAY_SCOPE, spray_key, s.worker_pin_rate_limit_window_seconds, max_attempts=10 / s.worker_pin_rate_limit_max_attempts)`
- **L1159** `intconst` n -> n+1 — `97a6aa3ca30d`
  - `auth_limiter.record(WORKER_PIN_SPRAY_SCOPE, spray_key, s.worker_pin_rate_limit_window_seconds, max_attempts=10 * s.worker_pin_rate_limit_max_attempts)`
  - `auth_limiter.record(WORKER_PIN_SPRAY_SCOPE, spray_key, s.worker_pin_rate_limit_window_seconds, max_attempts=11 * s.worker_pin_rate_limit_max_attempts)`
- **L1159** `intconst` n -> n-1 — `63d378abde2b`
  - `auth_limiter.record(WORKER_PIN_SPRAY_SCOPE, spray_key, s.worker_pin_rate_limit_window_seconds, max_attempts=10 * s.worker_pin_rate_limit_max_attempts)`
  - `auth_limiter.record(WORKER_PIN_SPRAY_SCOPE, spray_key, s.worker_pin_rate_limit_window_seconds, max_attempts=9 * s.worker_pin_rate_limit_max_attempts)`
- **L1314** `intconst` n -> n+1 — `b7a00fc79858`
  - `remaining = max(0, int((successor.expires_at - now).total_seconds()))`
  - `remaining = max(1, int((successor.expires_at - now).total_seconds()))`
- **L1461** `boolconst` True -> False — `117499e1cdc9`
  - `cookie_confirmed = True`
  - `cookie_confirmed = False`
- **L1488** `boolconst` True -> False — `b4634ad148f5`
  - `cookie_confirmed = True`
  - `cookie_confirmed = False`
- **L1506** `intconst` n -> n+1 — `ffac4aaa6e49`
  - `logged_out_user.token_version += 1`
  - `logged_out_user.token_version += 2`
- **L1656** `binop` Sub -> Add — `810484825b6b`
  - `remaining_affiliations = affiliation_cap - len(owned)`
  - `remaining_affiliations = affiliation_cap + len(owned)`
- **L1809** `intconst` n -> n+1 — `a19226e37286`
  - `locked_user.token_version += 1`
  - `locked_user.token_version += 2`
- **L1809** `intconst` n -> n-1 — `0ea9ebd21d33`
  - `locked_user.token_version += 1`
  - `locked_user.token_version += 0`
- **L2215** `intconst` n -> n+1 — `8d9402162013`
  - `raise HTTPException(status_code=400, detail='That code is not valid right now.')`
  - `raise HTTPException(status_code=401, detail='That code is not valid right now.')`
- **L2215** `intconst` n -> n-1 — `7e0f73ea0a43`
  - `raise HTTPException(status_code=400, detail='That code is not valid right now.')`
  - `raise HTTPException(status_code=399, detail='That code is not valid right now.')`
- **L2575** `ifexp` swap branches — `5a43c3bf906e`
  - `user_id = user.id if user is not None else challenge_user_id`
  - `user_id = challenge_user_id if user is not None else user.id`
- **L2641** `loopjump` continue -> break — `3a00c8636133`
  - `continue`
  - `break`
- **L2655** `loopjump` break -> continue — `18facaa67b67`
  - `break`
  - `continue`

### `app/api/buckets.py` (3 survivors)

- **L78** `compare` Eq -> NotEq — `818d98928040`
  - `latest_move = select(BucketMove.effective_date).where(BucketMove.animal_id == preview_animals.c.animal_id).order_by(BucketMove.moved_at.desc(), BucketMove.id.desc()).limit(1).correlate(preview_animals).lateral('bucket_latest_move')`
  - `latest_move = select(BucketMove.effective_date).where(BucketMove.animal_id != preview_animals.c.animal_id).order_by(BucketMove.moved_at.desc(), BucketMove.id.desc()).limit(1).correlate(preview_animals).lateral('bucket_latest_move')`
- **L80** `intconst` n -> n-1 — `a101ea386f7e`
  - `latest_move = select(BucketMove.effective_date).where(BucketMove.animal_id == preview_animals.c.animal_id).order_by(BucketMove.moved_at.desc(), BucketMove.id.desc()).limit(1).correlate(preview_animals).lateral('bucket_latest_move')`
  - `latest_move = select(BucketMove.effective_date).where(BucketMove.animal_id == preview_animals.c.animal_id).order_by(BucketMove.moved_at.desc(), BucketMove.id.desc()).limit(0).correlate(preview_animals).lateral('bucket_latest_move')`
- **L115** `intconst` n -> n+1 — `39814000b8cd`
  - `by_bucket.setdefault(row.current_bucket, []).append(BucketAnimalOut(id=row.animal_id, tag_number=row.tag_number, name=row.name, sex=row.sex, latest_weight_kg=row.latest_weight_kg, days_in_current_bucket=max((reference_date - bucket_started).days, 0)))`
  - `by_bucket.setdefault(row.current_bucket, []).append(BucketAnimalOut(id=row.animal_id, tag_number=row.tag_number, name=row.name, sex=row.sex, latest_weight_kg=row.latest_weight_kg, days_in_current_bucket=max((reference_date - bucket_started).days, 1)))`

### `app/api/dashboard.py` (6 survivors)

- **L203** `intconst` n -> n+1 — `3afe6e22e8be`
  - `overdue_take = min(len(overdue_rows), max(DASHBOARD_PREVIEW_LIMIT // 2, DASHBOARD_PREVIEW_LIMIT - len(upcoming_rows)))`
  - `overdue_take = min(len(overdue_rows), max(DASHBOARD_PREVIEW_LIMIT // 3, DASHBOARD_PREVIEW_LIMIT - len(upcoming_rows)))`
- **L355** `intconst` n -> n+1 — `09abf472df48`
  - `insurance_expiring_total = int(expiring_rows[0].preview_total) if expiring_rows else 0`
  - `insurance_expiring_total = int(expiring_rows[0].preview_total) if expiring_rows else 1`
- **L477** `boolop` Or -> And — `1d77734651e0`
  - `restricted_animals = [RestrictedAnimalOut(animal=_animal_identity_out(row[0]), current_bucket=row[0].current_bucket, held_since=placed_dates.get(row[0].id), reason=row[0].restriction_reason or row[0].suspected_disease if can_view_health else None) for row in held_rows]`
  - `restricted_animals = [RestrictedAnimalOut(animal=_animal_identity_out(row[0]), current_bucket=row[0].current_bucket, held_since=placed_dates.get(row[0].id), reason=row[0].restriction_reason and row[0].suspected_disease if can_view_health else None) for row in held_rows]`
- **L708** `binop` Div -> Mult — `7035eca3f16b`
  - `breeding_stats = BreedingStatsOut(total_records=total_records if can_view_breeding else None, conception_rate=_rate(conceived_count, completed_count) if can_view_breeding else None, first_cycle_rate=_rate(fc_conceived, fc_completed) if can_view_breeding else None, kiddings=kiddings_count if can_view_breeding else None, kids_per_kidding=round(float(total_alive) / kiddings_count, 2) if kiddings_count and can_view_breeding else None, twin_rate=_rate(multi_kid, kiddings_count) if can_view_breeding else None, cull_candidates=[_animal_identity_out(a) for a in cull_candidates], cull_candidates_total=cull_candidates_total, cull_candidates_limit=DASHBOARD_PREVIEW_LIMIT)`
  - `breeding_stats = BreedingStatsOut(total_records=total_records if can_view_breeding else None, conception_rate=_rate(conceived_count, completed_count) if can_view_breeding else None, first_cycle_rate=_rate(fc_conceived, fc_completed) if can_view_breeding else None, kiddings=kiddings_count if can_view_breeding else None, kids_per_kidding=round(float(total_alive) * kiddings_count, 2) if kiddings_count and can_view_breeding else None, twin_rate=_rate(multi_kid, kiddings_count) if can_view_breeding else None, cull_candidates=[_animal_identity_out(a) for a in cull_candidates], cull_candidates_total=cull_candidates_total, cull_candidates_limit=DASHBOARD_PREVIEW_LIMIT)`
- **L708** `intconst` n -> n+1 — `abe8420d2695`
  - `breeding_stats = BreedingStatsOut(total_records=total_records if can_view_breeding else None, conception_rate=_rate(conceived_count, completed_count) if can_view_breeding else None, first_cycle_rate=_rate(fc_conceived, fc_completed) if can_view_breeding else None, kiddings=kiddings_count if can_view_breeding else None, kids_per_kidding=round(float(total_alive) / kiddings_count, 2) if kiddings_count and can_view_breeding else None, twin_rate=_rate(multi_kid, kiddings_count) if can_view_breeding else None, cull_candidates=[_animal_identity_out(a) for a in cull_candidates], cull_candidates_total=cull_candidates_total, cull_candidates_limit=DASHBOARD_PREVIEW_LIMIT)`
  - `breeding_stats = BreedingStatsOut(total_records=total_records if can_view_breeding else None, conception_rate=_rate(conceived_count, completed_count) if can_view_breeding else None, first_cycle_rate=_rate(fc_conceived, fc_completed) if can_view_breeding else None, kiddings=kiddings_count if can_view_breeding else None, kids_per_kidding=round(float(total_alive) / kiddings_count, 3) if kiddings_count and can_view_breeding else None, twin_rate=_rate(multi_kid, kiddings_count) if can_view_breeding else None, cull_candidates=[_animal_identity_out(a) for a in cull_candidates], cull_candidates_total=cull_candidates_total, cull_candidates_limit=DASHBOARD_PREVIEW_LIMIT)`
- **L708** `intconst` n -> n-1 — `41fd09b82758`
  - `breeding_stats = BreedingStatsOut(total_records=total_records if can_view_breeding else None, conception_rate=_rate(conceived_count, completed_count) if can_view_breeding else None, first_cycle_rate=_rate(fc_conceived, fc_completed) if can_view_breeding else None, kiddings=kiddings_count if can_view_breeding else None, kids_per_kidding=round(float(total_alive) / kiddings_count, 2) if kiddings_count and can_view_breeding else None, twin_rate=_rate(multi_kid, kiddings_count) if can_view_breeding else None, cull_candidates=[_animal_identity_out(a) for a in cull_candidates], cull_candidates_total=cull_candidates_total, cull_candidates_limit=DASHBOARD_PREVIEW_LIMIT)`
  - `breeding_stats = BreedingStatsOut(total_records=total_records if can_view_breeding else None, conception_rate=_rate(conceived_count, completed_count) if can_view_breeding else None, first_cycle_rate=_rate(fc_conceived, fc_completed) if can_view_breeding else None, kiddings=kiddings_count if can_view_breeding else None, kids_per_kidding=round(float(total_alive) / kiddings_count, 1) if kiddings_count and can_view_breeding else None, twin_rate=_rate(multi_kid, kiddings_count) if can_view_breeding else None, cull_candidates=[_animal_identity_out(a) for a in cull_candidates], cull_candidates_total=cull_candidates_total, cull_candidates_limit=DASHBOARD_PREVIEW_LIMIT)`

### `app/api/finance.py` (6 survivors)

- **L297** `compare` GtE -> Gt — `f7117b1171db`
  - `old_is_latest = other_key is None or old_key >= other_key`
  - `old_is_latest = other_key is None or old_key > other_key`
- **L298** `compare` GtE -> Gt — `a28d0b70516e`
  - `corrected_is_latest = other_key is None or corrected_key >= other_key`
  - `corrected_is_latest = other_key is None or corrected_key > other_key`
- **L788** `boolop` Or -> And — `572e79a3bdce`
  - `replacement = Transaction(farm_id=farm.id, date=payload.date, type=payload.type, category=payload.category, amount=money(payload.amount), related_animal_id=animal_pk, notes=(payload.notes or '').strip() or None, created_by_id=user.id, source_type=txn.source_type, source_id=txn.source_id, feed_inventory_id=txn.feed_inventory_id, feed_quantity_kg=replacement_feed_quantity_kg, feed_unit_price_per_kg=replacement_feed_unit_price, correction_of_id=txn.id)`
  - `replacement = Transaction(farm_id=farm.id, date=payload.date, type=payload.type, category=payload.category, amount=money(payload.amount), related_animal_id=animal_pk, notes=(payload.notes or '').strip() and None, created_by_id=user.id, source_type=txn.source_type, source_id=txn.source_id, feed_inventory_id=txn.feed_inventory_id, feed_quantity_kg=replacement_feed_quantity_kg, feed_unit_price_per_kg=replacement_feed_unit_price, correction_of_id=txn.id)`
- **L838** `intconst` n -> n-1 — `6e13399074b3`
  - `@router.get('/insurance')
async def list_insurance_policies(db: DbSession, farm: CurrentFarm, perms: FinanceView, status: InsuranceStatusStr | None=None, animal_id: int | None=Query(default=None, ge=1, le=MAX_INT32_ID), limit: Annotated[int, Query(ge=1, le=200)]=200, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> InsuranceListOut:
    """The farm's insurance register, most urgent renewal first.

    A cross-farm ``animal_id`` filter simply matches nothing (it is a filter,
    not a resource lookup), so the list cannot serve as an enumeration
    oracle. ``total`` is the full filtered count for honest pagination."""
    query = select(InsurancePolicy).options(selectinload(InsurancePolicy.animal)).where(InsurancePolicy.farm_id == farm.id)
    if status is not None:
        query = query.where(InsurancePolicy.status == status)
    if animal_id is not None:
        query = query.where(InsurancePolicy.animal_id == animal_id)
    total = (await db.execute(select(func.count()).select_from(query.order_by(None).subquery()))).scalar_one()
    policies = list((await db.execute(query.order_by(InsurancePolicy.renewal_date, InsurancePolicy.id).offset(offset).limit(limit))).scalars().all())
    return InsuranceListOut(policies=[_policy_out(policy) for policy in policies], total=total)`
  - `@router.get('/insurance')
async def list_insurance_policies(db: DbSession, farm: CurrentFarm, perms: FinanceView, status: InsuranceStatusStr | None=None, animal_id: int | None=Query(default=None, ge=0, le=MAX_INT32_ID), limit: Annotated[int, Query(ge=1, le=200)]=200, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> InsuranceListOut:
    """The farm's insurance register, most urgent renewal first.

    A cross-farm ``animal_id`` filter simply matches nothing (it is a filter,
    not a resource lookup), so the list cannot serve as an enumeration
    oracle. ``total`` is the full filtered count for honest pagination."""
    query = select(InsurancePolicy).options(selectinload(InsurancePolicy.animal)).where(InsurancePolicy.farm_id == farm.id)
    if status is not None:
        query = query.where(InsurancePolicy.status == status)
    if animal_id is not None:
        query = query.where(InsurancePolicy.animal_id == animal_id)
    total = (await db.execute(select(func.count()).select_from(query.order_by(None).subquery()))).scalar_one()
    policies = list((await db.execute(query.order_by(InsurancePolicy.renewal_date, InsurancePolicy.id).offset(offset).limit(limit))).scalars().all())
    return InsuranceListOut(policies=[_policy_out(policy) for policy in policies], total=total)`
- **L969** `boolop` And -> Or — `c284d19f5838`
  - `out.animal_tag = linked.tag_number if linked is not None and linked.farm_id == farm.id else None`
  - `out.animal_tag = linked.tag_number if linked is not None or linked.farm_id == farm.id else None`
- **L1036** `compare` Eq -> NotEq — `97bdfdc4f73b`
  - `out.animal_tag = linked.tag_number if linked is not None and linked.farm_id == farm.id else None`
  - `out.animal_tag = linked.tag_number if linked is not None and linked.farm_id != farm.id else None`

### `app/api/health.py` (4 survivors)

- **L78** `intconst` n -> n+1 — `92e7e6574e37`
  - `HEALTH_LOOKUP_DEFAULT_LIMIT = 50`
  - `HEALTH_LOOKUP_DEFAULT_LIMIT = 51`
- **L78** `intconst` n -> n-1 — `865db8346495`
  - `HEALTH_LOOKUP_DEFAULT_LIMIT = 50`
  - `HEALTH_LOOKUP_DEFAULT_LIMIT = 49`
- **L155** `compare` LtE -> Lt — `3037c4efc9eb`
  - `stmt = stmt.where(Animal.id == int(numeric) if int(numeric) <= MAX_INT32_ID else false())`
  - `stmt = stmt.where(Animal.id == int(numeric) if int(numeric) < MAX_INT32_ID else false())`
- **L496** `compare` LtE -> Lt — `e8218e964762`
  - `filters.append(Animal.purchase_batch_id == target.purchase_batch_id if target.purchase_batch_id is not None and target.purchase_batch_id <= MAX_INT32_ID else false())`
  - `filters.append(Animal.purchase_batch_id == target.purchase_batch_id if target.purchase_batch_id is not None and target.purchase_batch_id < MAX_INT32_ID else false())`

### `app/api/kidding.py` (5 survivors)

- **L47** `intconst` n -> n-1 — `1674185717e2`
  - `DUE_MAX_LIMIT = 100`
  - `DUE_MAX_LIMIT = 99`
- **L76** `intconst` n -> n+1 — `7642956d4ea0`
  - `@router.get('')
async def kidding_list(db: DbSession, farm: CurrentFarm, perms: Annotated[set[str], Depends(require_perm('kidding.view'))], limit: Annotated[int, Query(ge=1, le=200)]=30, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0, upcoming_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)]=DUE_DEFAULT_LIMIT, upcoming_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0, overdue_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)]=DUE_DEFAULT_LIMIT, overdue_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> KiddingListOut:
    now = today(farm.timezone)
    horizon = now + timedelta(days=30)
    awaiting_where = (BreedingRecord.farm_id == farm.id, BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value, BreedingRecord.expected_kidding_date.is_not(None), Animal.status == AnimalStatus.ACTIVE.value, KiddingRecord.id.is_(None))

    async def due_page(*date_predicates: ColumnElement[bool], page_limit: int, page_offset: int) -> tuple[list[BreedingRecord], int]:
        joins = select(BreedingRecord).join(Animal, BreedingRecord.doe_id == Animal.id).outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id).where(*awaiting_where, *date_predicates)
        total = (await db.execute(select(func.count()).select_from(joins.subquery()))).scalar_one()
        rows = await db.execute(joins.options(selectinload(BreedingRecord.doe), selectinload(BreedingRecord.buck), selectinload(BreedingRecord.kidding_record)).order_by(BreedingRecord.expected_kidding_date, BreedingRecord.id).offset(page_offset).limit(page_limit))
        return (list(rows.scalars()), int(total))
    upcoming, upcoming_total = await due_page(BreedingRecord.expected_kidding_date >= now, BreedingRecord.expected_kidding_date <= horizon, page_limit=upcoming_limit, page_offset=upcoming_offset)
    overdue, overdue_total = await due_page(BreedingRecord.expected_kidding_date < now, page_limit=overdue_limit, page_offset=overdue_offset)
    history_result = await db.execute(select(KiddingRecord).options(selectinload(KiddingRecord.kids), selectinload(KiddingRecord.doe)).where(KiddingRecord.farm_id == farm.id).order_by(KiddingRecord.date.desc(), KiddingRecord.id.desc()).offset(offset).limit(limit))
    total = (await db.execute(select(func.count()).select_from(KiddingRecord).where(KiddingRecord.farm_id == farm.id))).scalar_one()
    return KiddingListOut(records=[_kidding_out(r) for r in history_result.scalars()], upcoming=[breeding_out(r) for r in upcoming], upcoming_total=upcoming_total, upcoming_limit=upcoming_limit, upcoming_offset=upcoming_offset, overdue=[breeding_out(r) for r in overdue], overdue_total=overdue_total, overdue_limit=overdue_limit, overdue_offset=overdue_offset, total=total, limit=limit, offset=offset)`
  - `@router.get('')
async def kidding_list(db: DbSession, farm: CurrentFarm, perms: Annotated[set[str], Depends(require_perm('kidding.view'))], limit: Annotated[int, Query(ge=1, le=200)]=31, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0, upcoming_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)]=DUE_DEFAULT_LIMIT, upcoming_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0, overdue_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)]=DUE_DEFAULT_LIMIT, overdue_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> KiddingListOut:
    now = today(farm.timezone)
    horizon = now + timedelta(days=30)
    awaiting_where = (BreedingRecord.farm_id == farm.id, BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value, BreedingRecord.expected_kidding_date.is_not(None), Animal.status == AnimalStatus.ACTIVE.value, KiddingRecord.id.is_(None))

    async def due_page(*date_predicates: ColumnElement[bool], page_limit: int, page_offset: int) -> tuple[list[BreedingRecord], int]:
        joins = select(BreedingRecord).join(Animal, BreedingRecord.doe_id == Animal.id).outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id).where(*awaiting_where, *date_predicates)
        total = (await db.execute(select(func.count()).select_from(joins.subquery()))).scalar_one()
        rows = await db.execute(joins.options(selectinload(BreedingRecord.doe), selectinload(BreedingRecord.buck), selectinload(BreedingRecord.kidding_record)).order_by(BreedingRecord.expected_kidding_date, BreedingRecord.id).offset(page_offset).limit(page_limit))
        return (list(rows.scalars()), int(total))
    upcoming, upcoming_total = await due_page(BreedingRecord.expected_kidding_date >= now, BreedingRecord.expected_kidding_date <= horizon, page_limit=upcoming_limit, page_offset=upcoming_offset)
    overdue, overdue_total = await due_page(BreedingRecord.expected_kidding_date < now, page_limit=overdue_limit, page_offset=overdue_offset)
    history_result = await db.execute(select(KiddingRecord).options(selectinload(KiddingRecord.kids), selectinload(KiddingRecord.doe)).where(KiddingRecord.farm_id == farm.id).order_by(KiddingRecord.date.desc(), KiddingRecord.id.desc()).offset(offset).limit(limit))
    total = (await db.execute(select(func.count()).select_from(KiddingRecord).where(KiddingRecord.farm_id == farm.id))).scalar_one()
    return KiddingListOut(records=[_kidding_out(r) for r in history_result.scalars()], upcoming=[breeding_out(r) for r in upcoming], upcoming_total=upcoming_total, upcoming_limit=upcoming_limit, upcoming_offset=upcoming_offset, overdue=[breeding_out(r) for r in overdue], overdue_total=overdue_total, overdue_limit=overdue_limit, overdue_offset=overdue_offset, total=total, limit=limit, offset=offset)`
- **L76** `intconst` n -> n-1 — `5edb2732c00f`
  - `@router.get('')
async def kidding_list(db: DbSession, farm: CurrentFarm, perms: Annotated[set[str], Depends(require_perm('kidding.view'))], limit: Annotated[int, Query(ge=1, le=200)]=30, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0, upcoming_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)]=DUE_DEFAULT_LIMIT, upcoming_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0, overdue_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)]=DUE_DEFAULT_LIMIT, overdue_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> KiddingListOut:
    now = today(farm.timezone)
    horizon = now + timedelta(days=30)
    awaiting_where = (BreedingRecord.farm_id == farm.id, BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value, BreedingRecord.expected_kidding_date.is_not(None), Animal.status == AnimalStatus.ACTIVE.value, KiddingRecord.id.is_(None))

    async def due_page(*date_predicates: ColumnElement[bool], page_limit: int, page_offset: int) -> tuple[list[BreedingRecord], int]:
        joins = select(BreedingRecord).join(Animal, BreedingRecord.doe_id == Animal.id).outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id).where(*awaiting_where, *date_predicates)
        total = (await db.execute(select(func.count()).select_from(joins.subquery()))).scalar_one()
        rows = await db.execute(joins.options(selectinload(BreedingRecord.doe), selectinload(BreedingRecord.buck), selectinload(BreedingRecord.kidding_record)).order_by(BreedingRecord.expected_kidding_date, BreedingRecord.id).offset(page_offset).limit(page_limit))
        return (list(rows.scalars()), int(total))
    upcoming, upcoming_total = await due_page(BreedingRecord.expected_kidding_date >= now, BreedingRecord.expected_kidding_date <= horizon, page_limit=upcoming_limit, page_offset=upcoming_offset)
    overdue, overdue_total = await due_page(BreedingRecord.expected_kidding_date < now, page_limit=overdue_limit, page_offset=overdue_offset)
    history_result = await db.execute(select(KiddingRecord).options(selectinload(KiddingRecord.kids), selectinload(KiddingRecord.doe)).where(KiddingRecord.farm_id == farm.id).order_by(KiddingRecord.date.desc(), KiddingRecord.id.desc()).offset(offset).limit(limit))
    total = (await db.execute(select(func.count()).select_from(KiddingRecord).where(KiddingRecord.farm_id == farm.id))).scalar_one()
    return KiddingListOut(records=[_kidding_out(r) for r in history_result.scalars()], upcoming=[breeding_out(r) for r in upcoming], upcoming_total=upcoming_total, upcoming_limit=upcoming_limit, upcoming_offset=upcoming_offset, overdue=[breeding_out(r) for r in overdue], overdue_total=overdue_total, overdue_limit=overdue_limit, overdue_offset=overdue_offset, total=total, limit=limit, offset=offset)`
  - `@router.get('')
async def kidding_list(db: DbSession, farm: CurrentFarm, perms: Annotated[set[str], Depends(require_perm('kidding.view'))], limit: Annotated[int, Query(ge=1, le=200)]=29, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0, upcoming_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)]=DUE_DEFAULT_LIMIT, upcoming_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0, overdue_limit: Annotated[int, Query(ge=1, le=DUE_MAX_LIMIT)]=DUE_DEFAULT_LIMIT, overdue_offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> KiddingListOut:
    now = today(farm.timezone)
    horizon = now + timedelta(days=30)
    awaiting_where = (BreedingRecord.farm_id == farm.id, BreedingRecord.outcome == BreedingOutcome.CONFIRMED_PREGNANT.value, BreedingRecord.expected_kidding_date.is_not(None), Animal.status == AnimalStatus.ACTIVE.value, KiddingRecord.id.is_(None))

    async def due_page(*date_predicates: ColumnElement[bool], page_limit: int, page_offset: int) -> tuple[list[BreedingRecord], int]:
        joins = select(BreedingRecord).join(Animal, BreedingRecord.doe_id == Animal.id).outerjoin(KiddingRecord, KiddingRecord.breeding_record_id == BreedingRecord.id).where(*awaiting_where, *date_predicates)
        total = (await db.execute(select(func.count()).select_from(joins.subquery()))).scalar_one()
        rows = await db.execute(joins.options(selectinload(BreedingRecord.doe), selectinload(BreedingRecord.buck), selectinload(BreedingRecord.kidding_record)).order_by(BreedingRecord.expected_kidding_date, BreedingRecord.id).offset(page_offset).limit(page_limit))
        return (list(rows.scalars()), int(total))
    upcoming, upcoming_total = await due_page(BreedingRecord.expected_kidding_date >= now, BreedingRecord.expected_kidding_date <= horizon, page_limit=upcoming_limit, page_offset=upcoming_offset)
    overdue, overdue_total = await due_page(BreedingRecord.expected_kidding_date < now, page_limit=overdue_limit, page_offset=overdue_offset)
    history_result = await db.execute(select(KiddingRecord).options(selectinload(KiddingRecord.kids), selectinload(KiddingRecord.doe)).where(KiddingRecord.farm_id == farm.id).order_by(KiddingRecord.date.desc(), KiddingRecord.id.desc()).offset(offset).limit(limit))
    total = (await db.execute(select(func.count()).select_from(KiddingRecord).where(KiddingRecord.farm_id == farm.id))).scalar_one()
    return KiddingListOut(records=[_kidding_out(r) for r in history_result.scalars()], upcoming=[breeding_out(r) for r in upcoming], upcoming_total=upcoming_total, upcoming_limit=upcoming_limit, upcoming_offset=upcoming_offset, overdue=[breeding_out(r) for r in overdue], overdue_total=overdue_total, overdue_limit=overdue_limit, overdue_offset=overdue_offset, total=total, limit=limit, offset=offset)`
- **L306** `intconst` n -> n+1 — `a5acbdfbb614`
  - `raise HTTPException(status_code=422, detail=f'{profile.young} birth weight must be between {profile.birth_weight_kg_range[0]:g} and {profile.birth_weight_kg_range[1]:g} kg — {kid.birth_weight:g} kg is not a credible newborn weight')`
  - `raise HTTPException(status_code=422, detail=f'{profile.young} birth weight must be between {profile.birth_weight_kg_range[1]:g} and {profile.birth_weight_kg_range[1]:g} kg — {kid.birth_weight:g} kg is not a credible newborn weight')`
- **L307** `intconst` n -> n-1 — `f4be25da7064`
  - `raise HTTPException(status_code=422, detail=f'{profile.young} birth weight must be between {profile.birth_weight_kg_range[0]:g} and {profile.birth_weight_kg_range[1]:g} kg — {kid.birth_weight:g} kg is not a credible newborn weight')`
  - `raise HTTPException(status_code=422, detail=f'{profile.young} birth weight must be between {profile.birth_weight_kg_range[0]:g} and {profile.birth_weight_kg_range[0]:g} kg — {kid.birth_weight:g} kg is not a credible newborn weight')`

### `app/api/ops_simulation.py` (9 survivors)

- **L99** `boolconst` False -> True — `2e8b9635e3df`
  - `errors = [{key: value for key, value in error.items() if key in ('type', 'loc', 'msg')} for error in exc.errors(include_url=False)]`
  - `errors = [{key: value for key, value in error.items() if key in ('type', 'loc', 'msg')} for error in exc.errors(include_url=True)]`
- **L101** `intconst` n -> n+1 — `7f021a8d3115`
  - `raise HTTPException(status_code=422, detail=errors[:3]) from exc`
  - `raise HTTPException(status_code=422, detail=errors[:4]) from exc`
- **L101** `intconst` n -> n-1 — `dfab458e9b5a`
  - `raise HTTPException(status_code=422, detail=errors[:3]) from exc`
  - `raise HTTPException(status_code=422, detail=errors[:2]) from exc`
- **L122** `binop` Mult -> Div — `16367e4ec76f`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 10) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
  - `cost = payload.horizon_days / (1 + len(payload.animals) // 10) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
- **L122** `binop` Add -> Sub — `5ba2644bd0bc`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 10) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
  - `cost = payload.horizon_days * (1 - len(payload.animals) // 10) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
- **L122** `intconst` n -> n+1 — `34793912a86b`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 10) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 10) * (2 + _BIRTH_AMPLIFICATION_FACTOR)`
- **L122** `intconst` n -> n-1 — `b9405f1842e8`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 10) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 10) * (0 + _BIRTH_AMPLIFICATION_FACTOR)`
- **L122** `intconst` n -> n+1 — `88cea783d05d`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 10) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 11) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
- **L122** `intconst` n -> n-1 — `5e50eff34e04`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 10) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`
  - `cost = payload.horizon_days * (1 + len(payload.animals) // 9) * (1 + _BIRTH_AMPLIFICATION_FACTOR)`

### `app/api/owner.py` (42 survivors)

- **L172** `intconst` n -> n+1 — `6c884a9f5447`
  - `animals: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in animal_rows}`
  - `animals: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[3], row[3]) for row in animal_rows}`
- **L172** `intconst` n -> n-1 — `b3f6c2f185c3`
  - `animals: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in animal_rows}`
  - `animals: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[2]) for row in animal_rows}`
- **L175** `intconst` n -> n+1 — `46f15210cca7`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in duty_rows}`
  - `duties: dict[int, tuple[int, int, int]] = {row[1]: (row[1], row[2], row[3]) for row in duty_rows}`
- **L175** `intconst` n -> n+1 — `07907e180cdd`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in duty_rows}`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[2], row[2], row[3]) for row in duty_rows}`
- **L175** `intconst` n -> n-1 — `fce23228316e`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in duty_rows}`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[0], row[2], row[3]) for row in duty_rows}`
- **L175** `intconst` n -> n+1 — `261a0ee6e2f9`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in duty_rows}`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[3], row[3]) for row in duty_rows}`
- **L175** `intconst` n -> n-1 — `c37259dd0232`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in duty_rows}`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[1], row[3]) for row in duty_rows}`
- **L175** `intconst` n -> n-1 — `929f2d567695`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[3]) for row in duty_rows}`
  - `duties: dict[int, tuple[int, int, int]] = {row[0]: (row[1], row[2], row[2]) for row in duty_rows}`
- **L177** `intconst` n -> n+1 — `343f8a92a481`
  - `flags: dict[int, int] = {row[0]: int(row[1]) for row in flag_rows}`
  - `flags: dict[int, int] = {row[1]: int(row[1]) for row in flag_rows}`
- **L177** `intconst` n -> n+1 — `e4563d444455`
  - `flags: dict[int, int] = {row[0]: int(row[1]) for row in flag_rows}`
  - `flags: dict[int, int] = {row[0]: int(row[2]) for row in flag_rows}`
- **L177** `intconst` n -> n-1 — `b99209e68356`
  - `flags: dict[int, int] = {row[0]: int(row[1]) for row in flag_rows}`
  - `flags: dict[int, int] = {row[0]: int(row[0]) for row in flag_rows}`
- **L181** `intconst` n -> n+1 — `a006b7dccf7e`
  - `amount = Decimal(total or 0)`
  - `amount = Decimal(total or 1)`
- **L203** `intconst` n -> n+1 — `cca2d196786d`
  - `out.append(OwnerFarmOverviewOut(farm_id=farm_id, farm_name=farm.name, timezone=farm.timezone, active_animals=int(active), overdue_duties=int(overdue), todays_duties_pending=int(due_pending), todays_duties_done=int(due_done), kidding_watch=int(watch), movement_restricted=int(restricted), open_screening_flags=flags.get(farm_id, 0), month_income=income.get(farm_id, Decimal(0)), month_expense=expense.get(farm_id, Decimal(0)), month_net=income.get(farm_id, Decimal(0)) - expense.get(farm_id, Decimal(0))))`
  - `out.append(OwnerFarmOverviewOut(farm_id=farm_id, farm_name=farm.name, timezone=farm.timezone, active_animals=int(active), overdue_duties=int(overdue), todays_duties_pending=int(due_pending), todays_duties_done=int(due_done), kidding_watch=int(watch), movement_restricted=int(restricted), open_screening_flags=flags.get(farm_id, 1), month_income=income.get(farm_id, Decimal(0)), month_expense=expense.get(farm_id, Decimal(0)), month_net=income.get(farm_id, Decimal(0)) - expense.get(farm_id, Decimal(0))))`
- **L217** `intconst` n -> n+1 — `bc882454c2f4`
  - `@router.get('/benchmarks')
async def owner_benchmarks(response: Response, db: DbSession, user: CurrentUser, days: Annotated[int, Query(ge=1, le=365)]=90) -> OwnerBenchmarksOut:
    """Per-farm performance figures over the trailing window, for ranking."""
    response.headers['Cache-Control'] = 'no-store'
    farms = _require_owned(await _owned_farms(db, user.id))
    farm_ids = [farm.id for farm in farms]
    farms_by_id = {farm.id: farm for farm in farms}
    window_start = cast(func.timezone(Farm.timezone, func.now()) - func.make_interval(0, 0, 0, days), Date)
    completed = BreedingRecord.outcome.in_(sorted(ASSESSED_OUTCOMES))
    conceived = BreedingRecord.outcome.in_(sorted(CONCEIVED_OUTCOMES))
    breeding_rows = (await db.execute(select(BreedingRecord.farm_id, func.count().filter(completed), func.count().filter(conceived)).join(Farm, BreedingRecord.farm_id == Farm.id).where(BreedingRecord.farm_id.in_(farm_ids), BreedingRecord.ultrasound_result_date >= window_start).group_by(BreedingRecord.farm_id))).all()
    mortality_rows = (await db.execute(select(KiddingRecord.farm_id, func.count(KidEntry.id), func.count(KidEntry.id).filter(KidEntry.status.in_([KidStatus.STILLBORN.value, KidStatus.DIED.value]))).join(Farm, KiddingRecord.farm_id == Farm.id).join(KidEntry, KidEntry.kidding_record_id == KiddingRecord.id).where(KiddingRecord.farm_id.in_(farm_ids), KiddingRecord.date >= window_start).group_by(KiddingRecord.farm_id))).all()
    weight_window = select(WeightRecord.farm_id.label('farm_id'), WeightRecord.animal_id.label('animal_id'), func.min(WeightRecord.weight_kg).label('first_weight'), func.max(WeightRecord.weight_kg).label('last_weight'), func.min(WeightRecord.date).label('first_date'), func.max(WeightRecord.date).label('last_date')).join(Farm, WeightRecord.farm_id == Farm.id).where(WeightRecord.farm_id.in_(farm_ids), WeightRecord.date >= window_start).group_by(WeightRecord.farm_id, WeightRecord.animal_id).having(func.count() >= 2).subquery()
    gain_rows = (await db.execute(select(weight_window.c.farm_id, func.avg((weight_window.c.last_weight - weight_window.c.first_weight) / func.nullif(cast(weight_window.c.last_date, Date) - cast(weight_window.c.first_date, Date) + 1, 0)), func.sum(weight_window.c.last_weight - weight_window.c.first_weight)).group_by(weight_window.c.farm_id))).all()
    feed_rows = (await db.execute(select(Transaction.farm_id, func.sum(Transaction.amount)).join(Farm, Transaction.farm_id == Farm.id).where(Transaction.farm_id.in_(farm_ids), Transaction.date >= window_start, Transaction.category == 'FEED').group_by(Transaction.farm_id))).all()
    sale_rows = (await db.execute(select(Animal.farm_id, func.count(), func.avg(Animal.sale_price - func.coalesce(Animal.purchase_price, 0))).join(Farm, Animal.farm_id == Farm.id).where(Animal.farm_id.in_(farm_ids), Animal.status == AnimalStatus.SOLD.value, Animal.status_date >= window_start).group_by(Animal.farm_id))).all()
    breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}
    mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}
    gains: dict[int, tuple[Any, Any]] = {row[0]: (row[1], row[2]) for row in gain_rows}
    feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}
    sales: dict[int, tuple[int, Any]] = {row[0]: (row[1], row[2]) for row in sale_rows}
    out = []
    for farm_id in farm_ids:
        assessed, conceptions = breeding.get(farm_id, (0, 0))
        born, dead_kids = mortality.get(farm_id, (0, 0))
        avg_gain, total_gain = gains.get(farm_id, (None, None))
        sold_count, avg_margin = sales.get(farm_id, (0, None))
        feed_cost = feed.get(farm_id, Decimal(0))
        cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None
        out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 2) if avg_margin is not None else None, animals_sold=int(sold_count)))
    return OwnerBenchmarksOut(days=days, farms=out)`
  - `@router.get('/benchmarks')
async def owner_benchmarks(response: Response, db: DbSession, user: CurrentUser, days: Annotated[int, Query(ge=1, le=365)]=91) -> OwnerBenchmarksOut:
    """Per-farm performance figures over the trailing window, for ranking."""
    response.headers['Cache-Control'] = 'no-store'
    farms = _require_owned(await _owned_farms(db, user.id))
    farm_ids = [farm.id for farm in farms]
    farms_by_id = {farm.id: farm for farm in farms}
    window_start = cast(func.timezone(Farm.timezone, func.now()) - func.make_interval(0, 0, 0, days), Date)
    completed = BreedingRecord.outcome.in_(sorted(ASSESSED_OUTCOMES))
    conceived = BreedingRecord.outcome.in_(sorted(CONCEIVED_OUTCOMES))
    breeding_rows = (await db.execute(select(BreedingRecord.farm_id, func.count().filter(completed), func.count().filter(conceived)).join(Farm, BreedingRecord.farm_id == Farm.id).where(BreedingRecord.farm_id.in_(farm_ids), BreedingRecord.ultrasound_result_date >= window_start).group_by(BreedingRecord.farm_id))).all()
    mortality_rows = (await db.execute(select(KiddingRecord.farm_id, func.count(KidEntry.id), func.count(KidEntry.id).filter(KidEntry.status.in_([KidStatus.STILLBORN.value, KidStatus.DIED.value]))).join(Farm, KiddingRecord.farm_id == Farm.id).join(KidEntry, KidEntry.kidding_record_id == KiddingRecord.id).where(KiddingRecord.farm_id.in_(farm_ids), KiddingRecord.date >= window_start).group_by(KiddingRecord.farm_id))).all()
    weight_window = select(WeightRecord.farm_id.label('farm_id'), WeightRecord.animal_id.label('animal_id'), func.min(WeightRecord.weight_kg).label('first_weight'), func.max(WeightRecord.weight_kg).label('last_weight'), func.min(WeightRecord.date).label('first_date'), func.max(WeightRecord.date).label('last_date')).join(Farm, WeightRecord.farm_id == Farm.id).where(WeightRecord.farm_id.in_(farm_ids), WeightRecord.date >= window_start).group_by(WeightRecord.farm_id, WeightRecord.animal_id).having(func.count() >= 2).subquery()
    gain_rows = (await db.execute(select(weight_window.c.farm_id, func.avg((weight_window.c.last_weight - weight_window.c.first_weight) / func.nullif(cast(weight_window.c.last_date, Date) - cast(weight_window.c.first_date, Date) + 1, 0)), func.sum(weight_window.c.last_weight - weight_window.c.first_weight)).group_by(weight_window.c.farm_id))).all()
    feed_rows = (await db.execute(select(Transaction.farm_id, func.sum(Transaction.amount)).join(Farm, Transaction.farm_id == Farm.id).where(Transaction.farm_id.in_(farm_ids), Transaction.date >= window_start, Transaction.category == 'FEED').group_by(Transaction.farm_id))).all()
    sale_rows = (await db.execute(select(Animal.farm_id, func.count(), func.avg(Animal.sale_price - func.coalesce(Animal.purchase_price, 0))).join(Farm, Animal.farm_id == Farm.id).where(Animal.farm_id.in_(farm_ids), Animal.status == AnimalStatus.SOLD.value, Animal.status_date >= window_start).group_by(Animal.farm_id))).all()
    breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}
    mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}
    gains: dict[int, tuple[Any, Any]] = {row[0]: (row[1], row[2]) for row in gain_rows}
    feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}
    sales: dict[int, tuple[int, Any]] = {row[0]: (row[1], row[2]) for row in sale_rows}
    out = []
    for farm_id in farm_ids:
        assessed, conceptions = breeding.get(farm_id, (0, 0))
        born, dead_kids = mortality.get(farm_id, (0, 0))
        avg_gain, total_gain = gains.get(farm_id, (None, None))
        sold_count, avg_margin = sales.get(farm_id, (0, None))
        feed_cost = feed.get(farm_id, Decimal(0))
        cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None
        out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 2) if avg_margin is not None else None, animals_sold=int(sold_count)))
    return OwnerBenchmarksOut(days=days, farms=out)`
- **L217** `intconst` n -> n-1 — `6007a31b7e6e`
  - `@router.get('/benchmarks')
async def owner_benchmarks(response: Response, db: DbSession, user: CurrentUser, days: Annotated[int, Query(ge=1, le=365)]=90) -> OwnerBenchmarksOut:
    """Per-farm performance figures over the trailing window, for ranking."""
    response.headers['Cache-Control'] = 'no-store'
    farms = _require_owned(await _owned_farms(db, user.id))
    farm_ids = [farm.id for farm in farms]
    farms_by_id = {farm.id: farm for farm in farms}
    window_start = cast(func.timezone(Farm.timezone, func.now()) - func.make_interval(0, 0, 0, days), Date)
    completed = BreedingRecord.outcome.in_(sorted(ASSESSED_OUTCOMES))
    conceived = BreedingRecord.outcome.in_(sorted(CONCEIVED_OUTCOMES))
    breeding_rows = (await db.execute(select(BreedingRecord.farm_id, func.count().filter(completed), func.count().filter(conceived)).join(Farm, BreedingRecord.farm_id == Farm.id).where(BreedingRecord.farm_id.in_(farm_ids), BreedingRecord.ultrasound_result_date >= window_start).group_by(BreedingRecord.farm_id))).all()
    mortality_rows = (await db.execute(select(KiddingRecord.farm_id, func.count(KidEntry.id), func.count(KidEntry.id).filter(KidEntry.status.in_([KidStatus.STILLBORN.value, KidStatus.DIED.value]))).join(Farm, KiddingRecord.farm_id == Farm.id).join(KidEntry, KidEntry.kidding_record_id == KiddingRecord.id).where(KiddingRecord.farm_id.in_(farm_ids), KiddingRecord.date >= window_start).group_by(KiddingRecord.farm_id))).all()
    weight_window = select(WeightRecord.farm_id.label('farm_id'), WeightRecord.animal_id.label('animal_id'), func.min(WeightRecord.weight_kg).label('first_weight'), func.max(WeightRecord.weight_kg).label('last_weight'), func.min(WeightRecord.date).label('first_date'), func.max(WeightRecord.date).label('last_date')).join(Farm, WeightRecord.farm_id == Farm.id).where(WeightRecord.farm_id.in_(farm_ids), WeightRecord.date >= window_start).group_by(WeightRecord.farm_id, WeightRecord.animal_id).having(func.count() >= 2).subquery()
    gain_rows = (await db.execute(select(weight_window.c.farm_id, func.avg((weight_window.c.last_weight - weight_window.c.first_weight) / func.nullif(cast(weight_window.c.last_date, Date) - cast(weight_window.c.first_date, Date) + 1, 0)), func.sum(weight_window.c.last_weight - weight_window.c.first_weight)).group_by(weight_window.c.farm_id))).all()
    feed_rows = (await db.execute(select(Transaction.farm_id, func.sum(Transaction.amount)).join(Farm, Transaction.farm_id == Farm.id).where(Transaction.farm_id.in_(farm_ids), Transaction.date >= window_start, Transaction.category == 'FEED').group_by(Transaction.farm_id))).all()
    sale_rows = (await db.execute(select(Animal.farm_id, func.count(), func.avg(Animal.sale_price - func.coalesce(Animal.purchase_price, 0))).join(Farm, Animal.farm_id == Farm.id).where(Animal.farm_id.in_(farm_ids), Animal.status == AnimalStatus.SOLD.value, Animal.status_date >= window_start).group_by(Animal.farm_id))).all()
    breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}
    mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}
    gains: dict[int, tuple[Any, Any]] = {row[0]: (row[1], row[2]) for row in gain_rows}
    feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}
    sales: dict[int, tuple[int, Any]] = {row[0]: (row[1], row[2]) for row in sale_rows}
    out = []
    for farm_id in farm_ids:
        assessed, conceptions = breeding.get(farm_id, (0, 0))
        born, dead_kids = mortality.get(farm_id, (0, 0))
        avg_gain, total_gain = gains.get(farm_id, (None, None))
        sold_count, avg_margin = sales.get(farm_id, (0, None))
        feed_cost = feed.get(farm_id, Decimal(0))
        cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None
        out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 2) if avg_margin is not None else None, animals_sold=int(sold_count)))
    return OwnerBenchmarksOut(days=days, farms=out)`
  - `@router.get('/benchmarks')
async def owner_benchmarks(response: Response, db: DbSession, user: CurrentUser, days: Annotated[int, Query(ge=1, le=365)]=89) -> OwnerBenchmarksOut:
    """Per-farm performance figures over the trailing window, for ranking."""
    response.headers['Cache-Control'] = 'no-store'
    farms = _require_owned(await _owned_farms(db, user.id))
    farm_ids = [farm.id for farm in farms]
    farms_by_id = {farm.id: farm for farm in farms}
    window_start = cast(func.timezone(Farm.timezone, func.now()) - func.make_interval(0, 0, 0, days), Date)
    completed = BreedingRecord.outcome.in_(sorted(ASSESSED_OUTCOMES))
    conceived = BreedingRecord.outcome.in_(sorted(CONCEIVED_OUTCOMES))
    breeding_rows = (await db.execute(select(BreedingRecord.farm_id, func.count().filter(completed), func.count().filter(conceived)).join(Farm, BreedingRecord.farm_id == Farm.id).where(BreedingRecord.farm_id.in_(farm_ids), BreedingRecord.ultrasound_result_date >= window_start).group_by(BreedingRecord.farm_id))).all()
    mortality_rows = (await db.execute(select(KiddingRecord.farm_id, func.count(KidEntry.id), func.count(KidEntry.id).filter(KidEntry.status.in_([KidStatus.STILLBORN.value, KidStatus.DIED.value]))).join(Farm, KiddingRecord.farm_id == Farm.id).join(KidEntry, KidEntry.kidding_record_id == KiddingRecord.id).where(KiddingRecord.farm_id.in_(farm_ids), KiddingRecord.date >= window_start).group_by(KiddingRecord.farm_id))).all()
    weight_window = select(WeightRecord.farm_id.label('farm_id'), WeightRecord.animal_id.label('animal_id'), func.min(WeightRecord.weight_kg).label('first_weight'), func.max(WeightRecord.weight_kg).label('last_weight'), func.min(WeightRecord.date).label('first_date'), func.max(WeightRecord.date).label('last_date')).join(Farm, WeightRecord.farm_id == Farm.id).where(WeightRecord.farm_id.in_(farm_ids), WeightRecord.date >= window_start).group_by(WeightRecord.farm_id, WeightRecord.animal_id).having(func.count() >= 2).subquery()
    gain_rows = (await db.execute(select(weight_window.c.farm_id, func.avg((weight_window.c.last_weight - weight_window.c.first_weight) / func.nullif(cast(weight_window.c.last_date, Date) - cast(weight_window.c.first_date, Date) + 1, 0)), func.sum(weight_window.c.last_weight - weight_window.c.first_weight)).group_by(weight_window.c.farm_id))).all()
    feed_rows = (await db.execute(select(Transaction.farm_id, func.sum(Transaction.amount)).join(Farm, Transaction.farm_id == Farm.id).where(Transaction.farm_id.in_(farm_ids), Transaction.date >= window_start, Transaction.category == 'FEED').group_by(Transaction.farm_id))).all()
    sale_rows = (await db.execute(select(Animal.farm_id, func.count(), func.avg(Animal.sale_price - func.coalesce(Animal.purchase_price, 0))).join(Farm, Animal.farm_id == Farm.id).where(Animal.farm_id.in_(farm_ids), Animal.status == AnimalStatus.SOLD.value, Animal.status_date >= window_start).group_by(Animal.farm_id))).all()
    breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}
    mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}
    gains: dict[int, tuple[Any, Any]] = {row[0]: (row[1], row[2]) for row in gain_rows}
    feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}
    sales: dict[int, tuple[int, Any]] = {row[0]: (row[1], row[2]) for row in sale_rows}
    out = []
    for farm_id in farm_ids:
        assessed, conceptions = breeding.get(farm_id, (0, 0))
        born, dead_kids = mortality.get(farm_id, (0, 0))
        avg_gain, total_gain = gains.get(farm_id, (None, None))
        sold_count, avg_margin = sales.get(farm_id, (0, None))
        feed_cost = feed.get(farm_id, Decimal(0))
        cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None
        out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 2) if avg_margin is not None else None, animals_sold=int(sold_count)))
    return OwnerBenchmarksOut(days=days, farms=out)`
- **L226** `intconst` n -> n+1 — `090db64d37dc`
  - `window_start = cast(func.timezone(Farm.timezone, func.now()) - func.make_interval(0, 0, 0, days), Date)`
  - `window_start = cast(func.timezone(Farm.timezone, func.now()) - func.make_interval(1, 0, 0, days), Date)`
- **L283** `compare` GtE -> Gt — `93124489d050`
  - `weight_window = select(WeightRecord.farm_id.label('farm_id'), WeightRecord.animal_id.label('animal_id'), func.min(WeightRecord.weight_kg).label('first_weight'), func.max(WeightRecord.weight_kg).label('last_weight'), func.min(WeightRecord.date).label('first_date'), func.max(WeightRecord.date).label('last_date')).join(Farm, WeightRecord.farm_id == Farm.id).where(WeightRecord.farm_id.in_(farm_ids), WeightRecord.date >= window_start).group_by(WeightRecord.farm_id, WeightRecord.animal_id).having(func.count() >= 2).subquery()`
  - `weight_window = select(WeightRecord.farm_id.label('farm_id'), WeightRecord.animal_id.label('animal_id'), func.min(WeightRecord.weight_kg).label('first_weight'), func.max(WeightRecord.weight_kg).label('last_weight'), func.min(WeightRecord.date).label('first_date'), func.max(WeightRecord.date).label('last_date')).join(Farm, WeightRecord.farm_id == Farm.id).where(WeightRecord.farm_id.in_(farm_ids), WeightRecord.date > window_start).group_by(WeightRecord.farm_id, WeightRecord.animal_id).having(func.count() >= 2).subquery()`
- **L341** `intconst` n -> n+1 — `a74c4fba15ef`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}`
  - `breeding: dict[int, tuple[int, int]] = {row[1]: (row[1], row[2]) for row in breeding_rows}`
- **L341** `intconst` n -> n+1 — `5f413b061166`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[2], row[2]) for row in breeding_rows}`
- **L341** `intconst` n -> n-1 — `02b9107f8436`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[0], row[2]) for row in breeding_rows}`
- **L341** `intconst` n -> n+1 — `35baaaf108e0`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[3]) for row in breeding_rows}`
- **L341** `intconst` n -> n-1 — `bd2b3e8c7b1d`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in breeding_rows}`
  - `breeding: dict[int, tuple[int, int]] = {row[0]: (row[1], row[1]) for row in breeding_rows}`
- **L342** `intconst` n -> n+1 — `f7866fea7d45`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}`
  - `mortality: dict[int, tuple[int, int]] = {row[1]: (row[1], row[2]) for row in mortality_rows}`
- **L342** `intconst` n -> n+1 — `4d7efa8e8fc9`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[2], row[2]) for row in mortality_rows}`
- **L342** `intconst` n -> n-1 — `a10d99512139`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[0], row[2]) for row in mortality_rows}`
- **L342** `intconst` n -> n+1 — `e182deb3521a`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[3]) for row in mortality_rows}`
- **L342** `intconst` n -> n-1 — `77d8c49905a9`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[2]) for row in mortality_rows}`
  - `mortality: dict[int, tuple[int, int]] = {row[0]: (row[1], row[1]) for row in mortality_rows}`
- **L343** `intconst` n -> n-1 — `1f99270dc55f`
  - `gains: dict[int, tuple[Any, Any]] = {row[0]: (row[1], row[2]) for row in gain_rows}`
  - `gains: dict[int, tuple[Any, Any]] = {row[0]: (row[1], row[1]) for row in gain_rows}`
- **L344** `intconst` n -> n+1 — `0e4f09bc957d`
  - `feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}`
  - `feed: dict[int, Decimal] = {row[1]: Decimal(row[1] or 0) for row in feed_rows}`
- **L344** `boolop` Or -> And — `7682084fe5e4`
  - `feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}`
  - `feed: dict[int, Decimal] = {row[0]: Decimal(row[1] and 0) for row in feed_rows}`
- **L344** `intconst` n -> n+1 — `539728aabf93`
  - `feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}`
  - `feed: dict[int, Decimal] = {row[0]: Decimal(row[2] or 0) for row in feed_rows}`
- **L344** `intconst` n -> n-1 — `39121c2a0303`
  - `feed: dict[int, Decimal] = {row[0]: Decimal(row[1] or 0) for row in feed_rows}`
  - `feed: dict[int, Decimal] = {row[0]: Decimal(row[0] or 0) for row in feed_rows}`
- **L353** `intconst` n -> n+1 — `3f92cb8cb47b`
  - `feed_cost = feed.get(farm_id, Decimal(0))`
  - `feed_cost = feed.get(farm_id, Decimal(1))`
- **L355** `ifexp` swap branches — `ff838350181b`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None`
  - `cost_per_kg = None if total_gain and float(total_gain) > 0 else round(float(feed_cost) / float(total_gain), 2)`
- **L355** `binop` Div -> Mult — `3fb606aefb05`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None`
  - `cost_per_kg = round(float(feed_cost) * float(total_gain), 2) if total_gain and float(total_gain) > 0 else None`
- **L355** `intconst` n -> n+1 — `657234372c5b`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 3) if total_gain and float(total_gain) > 0 else None`
- **L355** `intconst` n -> n-1 — `7dc558cfc48d`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 1) if total_gain and float(total_gain) > 0 else None`
- **L356** `compare` Gt -> GtE — `37a2e9a6ddb1`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) >= 0 else None`
- **L356** `intconst` n -> n+1 — `944670376d44`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 0 else None`
  - `cost_per_kg = round(float(feed_cost) / float(total_gain), 2) if total_gain and float(total_gain) > 1 else None`
- **L365** `intconst` n -> n+1 — `668629e25ffe`
  - `out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 2) if avg_margin is not None else None, animals_sold=int(sold_count)))`
  - `out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 4) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 2) if avg_margin is not None else None, animals_sold=int(sold_count)))`
- **L368** `intconst` n -> n+1 — `8f36f1707da8`
  - `out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 2) if avg_margin is not None else None, animals_sold=int(sold_count)))`
  - `out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 3) if avg_margin is not None else None, animals_sold=int(sold_count)))`
- **L368** `intconst` n -> n-1 — `d1b6b6600fe2`
  - `out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 2) if avg_margin is not None else None, animals_sold=int(sold_count)))`
  - `out.append(OwnerFarmBenchmarksOut(farm_id=farm_id, farm_name=farms_by_id[farm_id].name, conception_rate=_rate(conceptions, assessed), kid_mortality_rate=_rate(dead_kids, born), avg_daily_gain_kg=round(float(avg_gain), 3) if avg_gain is not None else None, feed_cost_per_kg_gain=cost_per_kg, profit_per_animal_sold=round(float(avg_margin), 1) if avg_margin is not None else None, animals_sold=int(sold_count)))`

### `app/api/planner.py` (20 survivors)

- **L63** `intconst` n -> n+1 — `c111bc0d6844`
  - `PLAN_QUOTA_LOCK_NAMESPACE = 4715`
  - `PLAN_QUOTA_LOCK_NAMESPACE = 4716`
- **L63** `intconst` n -> n-1 — `0fa953b0bc96`
  - `PLAN_QUOTA_LOCK_NAMESPACE = 4715`
  - `PLAN_QUOTA_LOCK_NAMESPACE = 4714`
- **L114** `boolconst` False -> True — `4e6565cf0c76`
  - `def _plan_out(plan: PlannerPlan, *, allow_invalid: bool=False) -> PlannerPlanOut:
    """ORM → schema; targets and assumptions are JSON text on the row."""
    try:
        targets, assumptions = _load_plan_parts(plan)
    except HTTPException as exc:
        if not allow_invalid:
            raise
        return PlannerPlanOut(id=plan.id, farm_id=plan.farm_id, name=plan.name, notes=plan.notes, start_year_month=plan.start_year_month, targets=None, assumptions=None, valid=False, validation_error=str(exc.detail), revision=plan.revision, created_at=plan.created_at, updated_at=plan.updated_at)
    return PlannerPlanOut(id=plan.id, farm_id=plan.farm_id, name=plan.name, notes=plan.notes, start_year_month=plan.start_year_month, targets=targets, assumptions=assumptions, valid=True, validation_error=None, revision=plan.revision, created_at=plan.created_at, updated_at=plan.updated_at)`
  - `def _plan_out(plan: PlannerPlan, *, allow_invalid: bool=True) -> PlannerPlanOut:
    """ORM → schema; targets and assumptions are JSON text on the row."""
    try:
        targets, assumptions = _load_plan_parts(plan)
    except HTTPException as exc:
        if not allow_invalid:
            raise
        return PlannerPlanOut(id=plan.id, farm_id=plan.farm_id, name=plan.name, notes=plan.notes, start_year_month=plan.start_year_month, targets=None, assumptions=None, valid=False, validation_error=str(exc.detail), revision=plan.revision, created_at=plan.created_at, updated_at=plan.updated_at)
    return PlannerPlanOut(id=plan.id, farm_id=plan.farm_id, name=plan.name, notes=plan.notes, start_year_month=plan.start_year_month, targets=targets, assumptions=assumptions, valid=True, validation_error=None, revision=plan.revision, created_at=plan.created_at, updated_at=plan.updated_at)`
- **L152** `boolconst` False -> True — `341ec062fb20`
  - `async def _get_plan(db: DbSession, farm_id: int, plan_id: int, *, for_update: bool=False) -> PlannerPlan:
    """Farm-scoped fetch: unknown or foreign ids both 404."""
    if not 1 <= plan_id <= MAX_INT32_ID:
        plan = None
    else:
        stmt = select(PlannerPlan).where(PlannerPlan.id == plan_id, PlannerPlan.farm_id == farm_id)
        if for_update:
            stmt = stmt.execution_options(populate_existing=True).with_for_update()
        plan = (await db.execute(stmt)).scalar_one_or_none()
    if plan is None or plan.farm_id != farm_id:
        raise HTTPException(status_code=404, detail='Plan not found')
    return plan`
  - `async def _get_plan(db: DbSession, farm_id: int, plan_id: int, *, for_update: bool=True) -> PlannerPlan:
    """Farm-scoped fetch: unknown or foreign ids both 404."""
    if not 1 <= plan_id <= MAX_INT32_ID:
        plan = None
    else:
        stmt = select(PlannerPlan).where(PlannerPlan.id == plan_id, PlannerPlan.farm_id == farm_id)
        if for_update:
            stmt = stmt.execution_options(populate_existing=True).with_for_update()
        plan = (await db.execute(stmt)).scalar_one_or_none()
    if plan is None or plan.farm_id != farm_id:
        raise HTTPException(status_code=404, detail='Plan not found')
    return plan`
- **L163** `boolconst` True -> False — `e4d9b9913d8e`
  - `stmt = stmt.execution_options(populate_existing=True).with_for_update()`
  - `stmt = stmt.execution_options(populate_existing=False).with_for_update()`
- **L170** `intconst` n -> n+1 — `debdf8f27440`
  - `async def _check_name_free(db: DbSession, farm_id: int, name: str, exclude_id: int=0) -> None:
    clash = (await db.execute(select(PlannerPlan).where(PlannerPlan.farm_id == farm_id, PlannerPlan.name == name, PlannerPlan.id != exclude_id))).scalars().first()
    if clash is not None:
        raise HTTPException(status_code=400, detail='A plan with that name already exists.')`
  - `async def _check_name_free(db: DbSession, farm_id: int, name: str, exclude_id: int=1) -> None:
    clash = (await db.execute(select(PlannerPlan).where(PlannerPlan.farm_id == farm_id, PlannerPlan.name == name, PlannerPlan.id != exclude_id))).scalars().first()
    if clash is not None:
        raise HTTPException(status_code=400, detail='A plan with that name already exists.')`
- **L200** `boolconst` True -> False — `30ab0451ef07`
  - `normalized = assumptions.model_copy(deep=True)`
  - `normalized = assumptions.model_copy(deep=False)`
- **L251** `intconst` n -> n+1 — `2c8a5b4b0cbb`
  - `horizon = min(240, max(payload.assumptions.meta.horizon_months, max(offsets), 12))`
  - `horizon = min(241, max(payload.assumptions.meta.horizon_months, max(offsets), 12))`
- **L251** `intconst` n -> n-1 — `35c75b8ebbf3`
  - `horizon = min(240, max(payload.assumptions.meta.horizon_months, max(offsets), 12))`
  - `horizon = min(239, max(payload.assumptions.meta.horizon_months, max(offsets), 12))`
- **L251** `intconst` n -> n+1 — `4bd90147e422`
  - `horizon = min(240, max(payload.assumptions.meta.horizon_months, max(offsets), 12))`
  - `horizon = min(240, max(payload.assumptions.meta.horizon_months, max(offsets), 13))`
- **L251** `intconst` n -> n-1 — `f9e5b85fca2e`
  - `horizon = min(240, max(payload.assumptions.meta.horizon_months, max(offsets), 12))`
  - `horizon = min(240, max(payload.assumptions.meta.horizon_months, max(offsets), 11))`
- **L260** `binop` Add -> Sub — `68d8709b2541`
  - `cost = (2 + close_gaps_passes + payload.risk_runs) * horizon`
  - `cost = (2 + close_gaps_passes - payload.risk_runs) * horizon`
- **L283** `intconst` n -> n+1 — `12f3915bb5c6`
  - `raise HTTPException(status_code=422, detail=f"This plan cannot be represented within the simulation's limits: {exc.errors()[:3]}") from exc`
  - `raise HTTPException(status_code=422, detail=f"This plan cannot be represented within the simulation's limits: {exc.errors()[:4]}") from exc`
- **L283** `intconst` n -> n-1 — `fab8f5dd99dc`
  - `raise HTTPException(status_code=422, detail=f"This plan cannot be represented within the simulation's limits: {exc.errors()[:3]}") from exc`
  - `raise HTTPException(status_code=422, detail=f"This plan cannot be represented within the simulation's limits: {exc.errors()[:2]}") from exc`
- **L382** `boolconst` True -> False — `9eb3839e4376`
  - `out = [_plan_out(plan, allow_invalid=True) for plan in result.scalars()]`
  - `out = [_plan_out(plan, allow_invalid=False) for plan in result.scalars()]`
- **L428** `boolconst` False -> True — `fddbfe3d898c`
  - `changed = False`
  - `changed = True`
- **L447** `boolconst` True -> False — `11d011b58daf`
  - `changed = True`
  - `changed = False`
- **L485** `binop` Mult -> Div — `aed9f32498a4`
  - `cost = 53 * assumptions.meta.horizon_months`
  - `cost = 53 / assumptions.meta.horizon_months`
- **L485** `intconst` n -> n+1 — `f5d1120f04ca`
  - `cost = 53 * assumptions.meta.horizon_months`
  - `cost = 54 * assumptions.meta.horizon_months`
- **L485** `intconst` n -> n-1 — `a7f1d5a498a9`
  - `cost = 53 * assumptions.meta.horizon_months`
  - `cost = 52 * assumptions.meta.horizon_months`

### `app/api/purchases.py` (3 survivors)

- **L88** `intconst` n -> n+1 — `7377ef061cc0`
  - `@router.get('')
async def list_batches(db: DbSession, farm: CurrentFarm, perms: PurchasesView, q: Annotated[PostgresText | None, Query(max_length=120)]=None, limit: Annotated[int, Query(ge=1, le=200)]=100, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> PurchaseBatchListOut:
    """Searched, paginated purchase batches for this farm, newest first.

    Text searches supplier names literally (LIKE wildcards are escaped); a
    numeric query, with an optional leading ``#``, also matches an exact batch
    id. This keeps selectors bounded without hiding old purchase batches.
    """
    base = select(PurchaseBatch).where(PurchaseBatch.farm_id == farm.id)
    if q and q.strip():
        raw = q.strip()
        escaped = raw.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        supplier_match = PurchaseBatch.supplier.ilike(f'%{escaped}%', escape='\\')
        numeric = raw.removeprefix('#')
        if numeric.isascii() and numeric.isdigit() and (int(numeric) <= MAX_INT32_ID):
            base = base.where(or_(supplier_match, PurchaseBatch.id == int(numeric)))
        else:
            base = base.where(supplier_match)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    result = await db.execute(base.order_by(PurchaseBatch.date.desc(), PurchaseBatch.id.desc()).offset(offset).limit(limit))
    return PurchaseBatchListOut(batches=await _batch_out(db, list(result.scalars().all())), total=total, limit=limit, offset=offset)`
  - `@router.get('')
async def list_batches(db: DbSession, farm: CurrentFarm, perms: PurchasesView, q: Annotated[PostgresText | None, Query(max_length=120)]=None, limit: Annotated[int, Query(ge=1, le=200)]=101, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> PurchaseBatchListOut:
    """Searched, paginated purchase batches for this farm, newest first.

    Text searches supplier names literally (LIKE wildcards are escaped); a
    numeric query, with an optional leading ``#``, also matches an exact batch
    id. This keeps selectors bounded without hiding old purchase batches.
    """
    base = select(PurchaseBatch).where(PurchaseBatch.farm_id == farm.id)
    if q and q.strip():
        raw = q.strip()
        escaped = raw.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        supplier_match = PurchaseBatch.supplier.ilike(f'%{escaped}%', escape='\\')
        numeric = raw.removeprefix('#')
        if numeric.isascii() and numeric.isdigit() and (int(numeric) <= MAX_INT32_ID):
            base = base.where(or_(supplier_match, PurchaseBatch.id == int(numeric)))
        else:
            base = base.where(supplier_match)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    result = await db.execute(base.order_by(PurchaseBatch.date.desc(), PurchaseBatch.id.desc()).offset(offset).limit(limit))
    return PurchaseBatchListOut(batches=await _batch_out(db, list(result.scalars().all())), total=total, limit=limit, offset=offset)`
- **L88** `intconst` n -> n-1 — `e1ac54c0c2c1`
  - `@router.get('')
async def list_batches(db: DbSession, farm: CurrentFarm, perms: PurchasesView, q: Annotated[PostgresText | None, Query(max_length=120)]=None, limit: Annotated[int, Query(ge=1, le=200)]=100, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> PurchaseBatchListOut:
    """Searched, paginated purchase batches for this farm, newest first.

    Text searches supplier names literally (LIKE wildcards are escaped); a
    numeric query, with an optional leading ``#``, also matches an exact batch
    id. This keeps selectors bounded without hiding old purchase batches.
    """
    base = select(PurchaseBatch).where(PurchaseBatch.farm_id == farm.id)
    if q and q.strip():
        raw = q.strip()
        escaped = raw.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        supplier_match = PurchaseBatch.supplier.ilike(f'%{escaped}%', escape='\\')
        numeric = raw.removeprefix('#')
        if numeric.isascii() and numeric.isdigit() and (int(numeric) <= MAX_INT32_ID):
            base = base.where(or_(supplier_match, PurchaseBatch.id == int(numeric)))
        else:
            base = base.where(supplier_match)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    result = await db.execute(base.order_by(PurchaseBatch.date.desc(), PurchaseBatch.id.desc()).offset(offset).limit(limit))
    return PurchaseBatchListOut(batches=await _batch_out(db, list(result.scalars().all())), total=total, limit=limit, offset=offset)`
  - `@router.get('')
async def list_batches(db: DbSession, farm: CurrentFarm, perms: PurchasesView, q: Annotated[PostgresText | None, Query(max_length=120)]=None, limit: Annotated[int, Query(ge=1, le=200)]=99, offset: Annotated[int, Query(ge=0, le=MAX_PAGE_OFFSET)]=0) -> PurchaseBatchListOut:
    """Searched, paginated purchase batches for this farm, newest first.

    Text searches supplier names literally (LIKE wildcards are escaped); a
    numeric query, with an optional leading ``#``, also matches an exact batch
    id. This keeps selectors bounded without hiding old purchase batches.
    """
    base = select(PurchaseBatch).where(PurchaseBatch.farm_id == farm.id)
    if q and q.strip():
        raw = q.strip()
        escaped = raw.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        supplier_match = PurchaseBatch.supplier.ilike(f'%{escaped}%', escape='\\')
        numeric = raw.removeprefix('#')
        if numeric.isascii() and numeric.isdigit() and (int(numeric) <= MAX_INT32_ID):
            base = base.where(or_(supplier_match, PurchaseBatch.id == int(numeric)))
        else:
            base = base.where(supplier_match)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    result = await db.execute(base.order_by(PurchaseBatch.date.desc(), PurchaseBatch.id.desc()).offset(offset).limit(limit))
    return PurchaseBatchListOut(batches=await _batch_out(db, list(result.scalars().all())), total=total, limit=limit, offset=offset)`
- **L209** `boolop` Or -> And — `918c7dbb7267`
  - `linked = getattr(linked, 'orig', None) or linked.__cause__`
  - `linked = getattr(linked, 'orig', None) and linked.__cause__`

### `app/api/screening.py` (38 survivors)

- **L74** `intconst` n -> n-1 — `88e295394c45`
  - `SCREENING_LIST_DEFAULT_LIMIT = 25`
  - `SCREENING_LIST_DEFAULT_LIMIT = 24`
- **L75** `intconst` n -> n+1 — `2f9cc4e9e602`
  - `SCREENING_LIST_MAX_LIMIT = 200`
  - `SCREENING_LIST_MAX_LIMIT = 201`
- **L75** `intconst` n -> n-1 — `bbd22cbc9113`
  - `SCREENING_LIST_MAX_LIMIT = 200`
  - `SCREENING_LIST_MAX_LIMIT = 199`
- **L81** `intconst` n -> n+1 — `a81d5413c22c`
  - `MAX_OPEN_SCREENING_BATCHES_PER_FARM = 5`
  - `MAX_OPEN_SCREENING_BATCHES_PER_FARM = 6`
- **L81** `intconst` n -> n-1 — `bd8c4f7db7bf`
  - `MAX_OPEN_SCREENING_BATCHES_PER_FARM = 5`
  - `MAX_OPEN_SCREENING_BATCHES_PER_FARM = 4`
- **L82** `intconst` n -> n+1 — `9a60c9a9b817`
  - `MAX_SCREENING_IMAGES_PER_BATCH = 100`
  - `MAX_SCREENING_IMAGES_PER_BATCH = 101`
- **L82** `intconst` n -> n-1 — `d902cf14f2df`
  - `MAX_SCREENING_IMAGES_PER_BATCH = 100`
  - `MAX_SCREENING_IMAGES_PER_BATCH = 99`
- **L83** `intconst` n -> n+1 — `d4623e57999d`
  - `MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM = 250`
  - `MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM = 251`
- **L83** `intconst` n -> n-1 — `9e24597e6b7c`
  - `MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM = 250`
  - `MAX_IN_FLIGHT_SCREENING_IMAGES_PER_FARM = 249`
- **L101** `intconst` n -> n+1 — `c1013f924921`
  - `SCREENING_INTAKE_LOCK_NAMESPACE = 4716`
  - `SCREENING_INTAKE_LOCK_NAMESPACE = 4717`
- **L101** `intconst` n -> n-1 — `5aea5a066803`
  - `SCREENING_INTAKE_LOCK_NAMESPACE = 4716`
  - `SCREENING_INTAKE_LOCK_NAMESPACE = 4715`
- **L202** `intconst` n -> n+1 — `f5103bb40ad5`
  - `rows = [ScreeningImageRowOut(id=image.id, status=cast(ScreeningImageStatusStr, image.status), bucket=cast(ScreeningBucketStr | None, image.bucket), batch_id=image.batch_id, s3_key=image.s3_key, captured_date=image.captured_date, width=image.width, height=image.height, byte_size=image.byte_size, error=image.error, created_at=image.created_at, latest_run=ScreeningRunOut.model_validate(latest[image.id]) if image.id in latest else None, pending_findings=pending.get(image.id, 0)) for image in images]`
  - `rows = [ScreeningImageRowOut(id=image.id, status=cast(ScreeningImageStatusStr, image.status), bucket=cast(ScreeningBucketStr | None, image.bucket), batch_id=image.batch_id, s3_key=image.s3_key, captured_date=image.captured_date, width=image.width, height=image.height, byte_size=image.byte_size, error=image.error, created_at=image.created_at, latest_run=ScreeningRunOut.model_validate(latest[image.id]) if image.id in latest else None, pending_findings=pending.get(image.id, 1)) for image in images]`
- **L353** `intconst` n -> n+1 — `87674cc88318`
  - `SCREENING_STATS_MAX_DAYS = 365`
  - `SCREENING_STATS_MAX_DAYS = 366`
- **L353** `intconst` n -> n-1 — `5c9ea837b04b`
  - `SCREENING_STATS_MAX_DAYS = 365`
  - `SCREENING_STATS_MAX_DAYS = 364`
- **L354** `intconst` n -> n+1 — `0c8308574382`
  - `SCREENING_EXPORT_MAX_RECORDS = 5000`
  - `SCREENING_EXPORT_MAX_RECORDS = 5001`
- **L354** `intconst` n -> n-1 — `2d20d5126daf`
  - `SCREENING_EXPORT_MAX_RECORDS = 5000`
  - `SCREENING_EXPORT_MAX_RECORDS = 4999`
- **L371** `binop` BitAnd -> BitOr — `8612d6198ec6`
  - `run_window = (ScreeningRun.farm_id == farm.id) & (ScreeningRun.created_at >= window_start)`
  - `run_window = (ScreeningRun.farm_id == farm.id) | (ScreeningRun.created_at >= window_start)`
- **L371** `compare` GtE -> Gt — `b84ee94cb03a`
  - `run_window = (ScreeningRun.farm_id == farm.id) & (ScreeningRun.created_at >= window_start)`
  - `run_window = (ScreeningRun.farm_id == farm.id) & (ScreeningRun.created_at > window_start)`
- **L462** `intconst` n -> n+1 — `c0bd23dcd62e`
  - `slot['gate_runs'] = int(runs or 0)`
  - `slot['gate_runs'] = int(runs or 1)`
- **L463** `intconst` n -> n+1 — `4a43e5c09e2c`
  - `slot['gate_flagged'] = int(flagged or 0)`
  - `slot['gate_flagged'] = int(flagged or 1)`
- **L464** `boolop` Or -> And — `a294ab459511`
  - `slot['gate_errors'] = int(errors or 0)`
  - `slot['gate_errors'] = int(errors and 0)`
- **L464** `intconst` n -> n+1 — `35cda33292e2`
  - `slot['gate_errors'] = int(errors or 0)`
  - `slot['gate_errors'] = int(errors or 1)`
- **L465** `ifexp` swap branches — `bee4ae0acc82`
  - `slot['avg_gate_latency_ms'] = int(avg_latency) if avg_latency is not None else None`
  - `slot['avg_gate_latency_ms'] = None if avg_latency is not None else int(avg_latency)`
- **L465** `compare` IsNot -> Is — `0161a7db2d2e`
  - `slot['avg_gate_latency_ms'] = int(avg_latency) if avg_latency is not None else None`
  - `slot['avg_gate_latency_ms'] = int(avg_latency) if avg_latency is None else None`
- **L467** `ifexp` swap branches — `41f44c93873a`
  - `slot['avg_gate_confidence'] = Decimal(avg_confidence).quantize(Decimal('0.001')) if avg_confidence is not None else None`
  - `slot['avg_gate_confidence'] = None if avg_confidence is not None else Decimal(avg_confidence).quantize(Decimal('0.001'))`
- **L468** `compare` IsNot -> Is — `bf9377f3e376`
  - `slot['avg_gate_confidence'] = Decimal(avg_confidence).quantize(Decimal('0.001')) if avg_confidence is not None else None`
  - `slot['avg_gate_confidence'] = Decimal(avg_confidence).quantize(Decimal('0.001')) if avg_confidence is None else None`
- **L473** `intconst` n -> n+1 — `56bb5d2598f2`
  - `slot['cross_checks'] = int(checks or 0)`
  - `slot['cross_checks'] = int(checks or 1)`
- **L474** `intconst` n -> n+1 — `6f4514ec4da4`
  - `slot['cross_check_agreements'] = int(agreements or 0)`
  - `slot['cross_check_agreements'] = int(agreements or 1)`
- **L477** `boolop` Or -> And — `e71daed448a6`
  - `slot['findings_confirmed'] = int(confirmed or 0)`
  - `slot['findings_confirmed'] = int(confirmed and 0)`
- **L477** `intconst` n -> n+1 — `2c5c5badc535`
  - `slot['findings_confirmed'] = int(confirmed or 0)`
  - `slot['findings_confirmed'] = int(confirmed or 1)`
- **L478** `boolop` Or -> And — `b427678d2d2f`
  - `slot['findings_rejected'] = int(rejected or 0)`
  - `slot['findings_rejected'] = int(rejected and 0)`
- **L478** `intconst` n -> n+1 — `4020929c44e3`
  - `slot['findings_rejected'] = int(rejected or 0)`
  - `slot['findings_rejected'] = int(rejected or 1)`
- **L479** `intconst` n -> n+1 — `ed9e4d9d00f5`
  - `slot['findings_pending'] = int(pending or 0)`
  - `slot['findings_pending'] = int(pending or 1)`
- **L624** `intconst` n -> n+1 — `6f2cf6f6165d`
  - `bucket_progress = progress['buckets'].setdefault(bucket, {'uploaded': 0, 'screened': 0, 'flagged': 0})`
  - `bucket_progress = progress['buckets'].setdefault(bucket, {'uploaded': 1, 'screened': 0, 'flagged': 0})`
- **L911** `intconst` n -> n+1 — `c63ef1f35053`
  - `key = f'{settings.screening_s3_prefix}/{farm.id}/{captured_date.isoformat()}/{payload.bucket}/{batch.id}-{uuid.uuid4().hex[:12]}{extension}'`
  - `key = f'{settings.screening_s3_prefix}/{farm.id}/{captured_date.isoformat()}/{payload.bucket}/{batch.id}-{uuid.uuid4().hex[:13]}{extension}'`
- **L911** `intconst` n -> n-1 — `37fb1a6bf9d8`
  - `key = f'{settings.screening_s3_prefix}/{farm.id}/{captured_date.isoformat()}/{payload.bucket}/{batch.id}-{uuid.uuid4().hex[:12]}{extension}'`
  - `key = f'{settings.screening_s3_prefix}/{farm.id}/{captured_date.isoformat()}/{payload.bucket}/{batch.id}-{uuid.uuid4().hex[:11]}{extension}'`
- **L913** `intconst` n -> n+1 — `d3d398c37d31`
  - `upload_token = secrets.token_urlsafe(32)`
  - `upload_token = secrets.token_urlsafe(33)`
- **L913** `intconst` n -> n-1 — `79254cd829f4`
  - `upload_token = secrets.token_urlsafe(32)`
  - `upload_token = secrets.token_urlsafe(31)`

### `app/api/simulation.py` (16 survivors)

- **L77** `intconst` n -> n+1 — `d1174b1004ac`
  - `SCENARIO_QUOTA_LOCK_NAMESPACE = 4713`
  - `SCENARIO_QUOTA_LOCK_NAMESPACE = 4714`
- **L77** `intconst` n -> n-1 — `9fd405a15f6d`
  - `SCENARIO_QUOTA_LOCK_NAMESPACE = 4713`
  - `SCENARIO_QUOTA_LOCK_NAMESPACE = 4712`
- **L146** `boolconst` False -> True — `c7eb69cd9194`
  - `async def _get_scenario(db: DbSession, farm_id: int, scenario_id: int, *, for_update: bool=False) -> SimulationScenario:
    """Farm-scoped fetch: unknown or foreign ids both 404."""
    if not 1 <= scenario_id <= MAX_INT32_ID:
        scenario = None
    else:
        stmt = select(SimulationScenario).where(SimulationScenario.id == scenario_id, SimulationScenario.farm_id == farm_id)
        if for_update:
            stmt = stmt.execution_options(populate_existing=True).with_for_update()
        scenario = (await db.execute(stmt)).scalar_one_or_none()
    if scenario is None or scenario.farm_id != farm_id:
        raise HTTPException(status_code=404, detail='Scenario not found')
    return scenario`
  - `async def _get_scenario(db: DbSession, farm_id: int, scenario_id: int, *, for_update: bool=True) -> SimulationScenario:
    """Farm-scoped fetch: unknown or foreign ids both 404."""
    if not 1 <= scenario_id <= MAX_INT32_ID:
        scenario = None
    else:
        stmt = select(SimulationScenario).where(SimulationScenario.id == scenario_id, SimulationScenario.farm_id == farm_id)
        if for_update:
            stmt = stmt.execution_options(populate_existing=True).with_for_update()
        scenario = (await db.execute(stmt)).scalar_one_or_none()
    if scenario is None or scenario.farm_id != farm_id:
        raise HTTPException(status_code=404, detail='Scenario not found')
    return scenario`
- **L157** `boolconst` True -> False — `b4fa775805a8`
  - `stmt = stmt.execution_options(populate_existing=True).with_for_update()`
  - `stmt = stmt.execution_options(populate_existing=False).with_for_update()`
- **L252** `intconst` n -> n-1 — `e0fa140b8783`
  - `_SENSITIVITY_PASSES = 19`
  - `_SENSITIVITY_PASSES = 18`
- **L340** `binop` Sub -> Add — `f5da9f0dd2fe`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) + case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
- **L340** `intconst` n -> n+1 — `15a1297806bb`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 13 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
- **L340** `intconst` n -> n-1 — `22bb10db46e3`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 11 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
- **L343** `compare` Gt -> GtE — `58dfdc6bb727`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) >= reference_date.day, 1), else_=0)`
- **L343** `intconst` n -> n-1 — `01d392ac2619`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 0), else_=0)`
- **L343** `intconst` n -> n+1 — `8c24e7193f84`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', dob)) * 12 + reference_date.month - func.extract('month', dob) - case((func.extract('day', dob) > reference_date.day, 1), else_=1)`
- **L402** `intconst` n -> n+1 — `279b00dec04a`
  - `@router.get('/calibration', responses={500: {'model': ErrorOut, 'description': 'Calibration data is internally inconsistent'}})
async def farm_calibration(db: DbSession, farm: CurrentFarm, sim_perms: SimView, animal_perms: AnimalsView, breeding_perms: BreedingView, kidding_perms: KiddingView, feeding_perms: FeedingView, finance_perms: FinanceView, breed: str='osmanabadi', system: System='stall_fed', lookback_months: Annotated[int, Query(ge=6, le=60)]=24) -> FarmCalibrationOut:
    """Calibrate a complete model from this farm's operational evidence.

    The endpoint reads animal, breeding, kidding, feeding and finance history,
    so each corresponding view permission is required in addition to
    ``simulation.view``. Results are advisory and never mutate a saved scenario
    or farm record.
    """
    try:
        return await calibrate_farm_assumptions(db, farm, breed=breed, system=system, lookback_months=lookback_months)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail='Calibration produced an assumption set the model rejects.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None`
  - `@router.get('/calibration', responses={500: {'model': ErrorOut, 'description': 'Calibration data is internally inconsistent'}})
async def farm_calibration(db: DbSession, farm: CurrentFarm, sim_perms: SimView, animal_perms: AnimalsView, breeding_perms: BreedingView, kidding_perms: KiddingView, feeding_perms: FeedingView, finance_perms: FinanceView, breed: str='osmanabadi', system: System='stall_fed', lookback_months: Annotated[int, Query(ge=6, le=60)]=25) -> FarmCalibrationOut:
    """Calibrate a complete model from this farm's operational evidence.

    The endpoint reads animal, breeding, kidding, feeding and finance history,
    so each corresponding view permission is required in addition to
    ``simulation.view``. Results are advisory and never mutate a saved scenario
    or farm record.
    """
    try:
        return await calibrate_farm_assumptions(db, farm, breed=breed, system=system, lookback_months=lookback_months)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail='Calibration produced an assumption set the model rejects.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None`
- **L402** `intconst` n -> n-1 — `ca75fbf9021c`
  - `@router.get('/calibration', responses={500: {'model': ErrorOut, 'description': 'Calibration data is internally inconsistent'}})
async def farm_calibration(db: DbSession, farm: CurrentFarm, sim_perms: SimView, animal_perms: AnimalsView, breeding_perms: BreedingView, kidding_perms: KiddingView, feeding_perms: FeedingView, finance_perms: FinanceView, breed: str='osmanabadi', system: System='stall_fed', lookback_months: Annotated[int, Query(ge=6, le=60)]=24) -> FarmCalibrationOut:
    """Calibrate a complete model from this farm's operational evidence.

    The endpoint reads animal, breeding, kidding, feeding and finance history,
    so each corresponding view permission is required in addition to
    ``simulation.view``. Results are advisory and never mutate a saved scenario
    or farm record.
    """
    try:
        return await calibrate_farm_assumptions(db, farm, breed=breed, system=system, lookback_months=lookback_months)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail='Calibration produced an assumption set the model rejects.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None`
  - `@router.get('/calibration', responses={500: {'model': ErrorOut, 'description': 'Calibration data is internally inconsistent'}})
async def farm_calibration(db: DbSession, farm: CurrentFarm, sim_perms: SimView, animal_perms: AnimalsView, breeding_perms: BreedingView, kidding_perms: KiddingView, feeding_perms: FeedingView, finance_perms: FinanceView, breed: str='osmanabadi', system: System='stall_fed', lookback_months: Annotated[int, Query(ge=6, le=60)]=23) -> FarmCalibrationOut:
    """Calibrate a complete model from this farm's operational evidence.

    The endpoint reads animal, breeding, kidding, feeding and finance history,
    so each corresponding view permission is required in addition to
    ``simulation.view``. Results are advisory and never mutate a saved scenario
    or farm record.
    """
    try:
        return await calibrate_farm_assumptions(db, farm, breed=breed, system=system, lookback_months=lookback_months)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail='Calibration produced an assumption set the model rejects.') from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None`
- **L607** `boolconst` False -> True — `dc7464022bc5`
  - `cost = sum((_run_cost(a, False, False, False) for a in loaded))`
  - `cost = sum((_run_cost(a, True, False, False) for a in loaded))`
- **L643** `boolconst` False -> True — `7acbe923cbbf`
  - `changed = False`
  - `changed = True`
- **L650** `boolconst` True -> False — `28308767f285`
  - `changed = True`
  - `changed = False`

### `app/api/tasks.py` (2 survivors)

- **L760** `boolop` And -> Or — `85c8f1fedfec`
  - `doe_active = any((animal.id == task.animal_id and animal.status == AnimalStatus.ACTIVE.value for animal in locked_animals))`
  - `doe_active = any((animal.id == task.animal_id or animal.status == AnimalStatus.ACTIVE.value for animal in locked_animals))`
- **L818** `boolop` And -> Or — `0c9d13cd807a`
  - `spawn_successor = task.recur_days is not None and (task.animal_id is None or any((animal.id == task.animal_id and animal.status == AnimalStatus.ACTIVE.value for animal in locked_animals)))`
  - `spawn_successor = task.recur_days is not None or (task.animal_id is None or any((animal.id == task.animal_id and animal.status == AnimalStatus.ACTIVE.value for animal in locked_animals)))`

### `app/api/team.py` (4 survivors)

- **L499** `boolconst` False -> True — `3205c4db088d`
  - `reset_policy = (False, RESET_PASSWORD_OWNER_ONLY_REASON)`
  - `reset_policy = (True, RESET_PASSWORD_OWNER_ONLY_REASON)`
- **L556** `boolconst` True -> False — `656fbd962f9f`
  - `statement = statement.execution_options(populate_existing=True)`
  - `statement = statement.execution_options(populate_existing=False)`
- **L580** `boolconst` True -> False — `35c1ff4c899d`
  - `statement = statement.execution_options(populate_existing=True)`
  - `statement = statement.execution_options(populate_existing=False)`
- **L1324** `intconst` n -> n+1 — `e1ac983a0d2b`
  - `locked_user.token_version += 1`
  - `locked_user.token_version += 2`

### `app/core/config.py` (500 survivors)

- **L54** `intconst` n -> n+1 — `24edfca63c91`
  - `MAX_PREVIOUS_JWT_PUBLIC_KEYS = 3`
  - `MAX_PREVIOUS_JWT_PUBLIC_KEYS = 4`
- **L54** `intconst` n -> n-1 — `55a880363e22`
  - `MAX_PREVIOUS_JWT_PUBLIC_KEYS = 3`
  - `MAX_PREVIOUS_JWT_PUBLIC_KEYS = 2`
- **L55** `intconst` n -> n-1 — `24e25aaaf116`
  - `MAX_PREVIOUS_IDEMPOTENCY_HMAC_SECRETS = 3`
  - `MAX_PREVIOUS_IDEMPOTENCY_HMAC_SECRETS = 2`
- **L56** `intconst` n -> n+1 — `63efe129cf83`
  - `MAX_PREVIOUS_TOTP_ENCRYPTION_KEYS = 3`
  - `MAX_PREVIOUS_TOTP_ENCRYPTION_KEYS = 4`
- **L56** `intconst` n -> n-1 — `35d7f7119e4b`
  - `MAX_PREVIOUS_TOTP_ENCRYPTION_KEYS = 3`
  - `MAX_PREVIOUS_TOTP_ENCRYPTION_KEYS = 2`
- **L394** `intconst` n -> n-1 — `18d87044ca59`
  - `name: str = Field(min_length=1, max_length=40)`
  - `name: str = Field(min_length=0, max_length=40)`
- **L394** `intconst` n -> n+1 — `15b84610ddaa`
  - `name: str = Field(min_length=1, max_length=40)`
  - `name: str = Field(min_length=1, max_length=41)`
- **L394** `intconst` n -> n-1 — `bec0bc584149`
  - `name: str = Field(min_length=1, max_length=40)`
  - `name: str = Field(min_length=1, max_length=39)`
- **L397** `intconst` n -> n-1 — `a3728a094081`
  - `model: str = Field(min_length=1, max_length=120)`
  - `model: str = Field(min_length=0, max_length=120)`
- **L397** `intconst` n -> n+1 — `bd567d373d58`
  - `model: str = Field(min_length=1, max_length=120)`
  - `model: str = Field(min_length=1, max_length=121)`
- **L397** `intconst` n -> n-1 — `3a37362e7448`
  - `model: str = Field(min_length=1, max_length=120)`
  - `model: str = Field(min_length=1, max_length=119)`
- **L486** `boolconst` True -> False — `2bc5f57f5f94`
  - `decoded = base64.b64decode(raw + '=' * (-len(raw) % 4), altchars=b'-_', validate=True)`
  - `decoded = base64.b64decode(raw + '=' * (-len(raw) % 4), altchars=b'-_', validate=False)`
- **L538** `intconst` n -> n-1 — `2f6e8a6bc6a1`
  - `provider_count = max(1, len(settings.screening_provider_rotation))`
  - `provider_count = max(0, len(settings.screening_provider_rotation))`
- **L539** `binop` Mult -> Div — `e4e00669b17b`
  - `worst_cascade_seconds = (2 * provider_count + 6) * settings.screening_provider_timeout_seconds`
  - `worst_cascade_seconds = (2 / provider_count + 6) * settings.screening_provider_timeout_seconds`
- **L539** `intconst` n -> n-1 — `5243c9f7b8e5`
  - `worst_cascade_seconds = (2 * provider_count + 6) * settings.screening_provider_timeout_seconds`
  - `worst_cascade_seconds = (1 * provider_count + 6) * settings.screening_provider_timeout_seconds`
- **L586** `intconst` n -> n+1 — `db9d390cbdf0`
  - `db_pool_size: int = Field(default=5, ge=1)`
  - `db_pool_size: int = Field(default=6, ge=1)`
- **L586** `intconst` n -> n-1 — `cebd86145a09`
  - `db_pool_size: int = Field(default=5, ge=1)`
  - `db_pool_size: int = Field(default=4, ge=1)`
- **L586** `intconst` n -> n+1 — `3dfef4da7f1f`
  - `db_pool_size: int = Field(default=5, ge=1)`
  - `db_pool_size: int = Field(default=5, ge=2)`
- **L586** `intconst` n -> n-1 — `7e1138795c4f`
  - `db_pool_size: int = Field(default=5, ge=1)`
  - `db_pool_size: int = Field(default=5, ge=0)`
- **L587** `intconst` n -> n+1 — `9bbda7c0c7a3`
  - `db_max_overflow: int = Field(default=10, ge=0)`
  - `db_max_overflow: int = Field(default=11, ge=0)`
- **L587** `intconst` n -> n-1 — `086b66b2a7aa`
  - `db_max_overflow: int = Field(default=10, ge=0)`
  - `db_max_overflow: int = Field(default=9, ge=0)`
- **L587** `intconst` n -> n+1 — `02dec7adb458`
  - `db_max_overflow: int = Field(default=10, ge=0)`
  - `db_max_overflow: int = Field(default=10, ge=1)`
- **L588** `intconst` n -> n+1 — `e8d1cacf6f08`
  - `db_pool_timeout: int = Field(default=30, ge=1)`
  - `db_pool_timeout: int = Field(default=31, ge=1)`
- **L588** `intconst` n -> n-1 — `c9fcbf710715`
  - `db_pool_timeout: int = Field(default=30, ge=1)`
  - `db_pool_timeout: int = Field(default=29, ge=1)`
- **L588** `intconst` n -> n+1 — `05bb19d9517f`
  - `db_pool_timeout: int = Field(default=30, ge=1)`
  - `db_pool_timeout: int = Field(default=30, ge=2)`
- **L588** `intconst` n -> n-1 — `e8995c252728`
  - `db_pool_timeout: int = Field(default=30, ge=1)`
  - `db_pool_timeout: int = Field(default=30, ge=0)`
- **L589** `intconst` n -> n+1 — `ec72bbcb0b4c`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=1)`
  - `db_statement_timeout_ms: int = Field(default=30001, ge=1)`
- **L589** `intconst` n -> n-1 — `ade5fdbed026`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=1)`
  - `db_statement_timeout_ms: int = Field(default=29999, ge=1)`
- **L589** `intconst` n -> n+1 — `53e940079f26`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=1)`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=2)`
- **L589** `intconst` n -> n-1 — `69f9fd3c0d0f`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=1)`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=0)`
- **L597** `intconst` n -> n+1 — `1e19276bc402`
  - `migration_statement_timeout_ms: int = Field(default=0, ge=0)`
  - `migration_statement_timeout_ms: int = Field(default=1, ge=0)`
- **L601** `intconst` n -> n+1 — `e80f44cd0f1e`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=2, le=24 * 90)`
- **L601** `intconst` n -> n-1 — `b74e7f3d1c67`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=0, le=24 * 90)`
- **L601** `intconst` n -> n+1 — `2c0a8126f9dd`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)`
  - `idempotency_retention_hours: int = Field(default=25 * 7, ge=1, le=24 * 90)`
- **L601** `intconst` n -> n-1 — `31c49dd57822`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)`
  - `idempotency_retention_hours: int = Field(default=23 * 7, ge=1, le=24 * 90)`
- **L601** `intconst` n -> n+1 — `3039c20b45e6`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)`
  - `idempotency_retention_hours: int = Field(default=24 * 8, ge=1, le=24 * 90)`
- **L601** `intconst` n -> n-1 — `16e88ed41ef7`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)`
  - `idempotency_retention_hours: int = Field(default=24 * 6, ge=1, le=24 * 90)`
- **L601** `intconst` n -> n+1 — `c48e9a049413`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 91)`
- **L601** `intconst` n -> n-1 — `47625ae6120b`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 90)`
  - `idempotency_retention_hours: int = Field(default=24 * 7, ge=1, le=24 * 89)`
- **L602** `intconst` n -> n+1 — `cf18f311ce39`
  - `idempotency_cleanup_interval_seconds: int = Field(default=3600, ge=60)`
  - `idempotency_cleanup_interval_seconds: int = Field(default=3601, ge=60)`
- **L602** `intconst` n -> n-1 — `89782f3908da`
  - `idempotency_cleanup_interval_seconds: int = Field(default=3600, ge=60)`
  - `idempotency_cleanup_interval_seconds: int = Field(default=3599, ge=60)`
- **L602** `intconst` n -> n+1 — `7360ac3b1222`
  - `idempotency_cleanup_interval_seconds: int = Field(default=3600, ge=60)`
  - `idempotency_cleanup_interval_seconds: int = Field(default=3600, ge=61)`
- **L602** `intconst` n -> n-1 — `c2e3bb89e80a`
  - `idempotency_cleanup_interval_seconds: int = Field(default=3600, ge=60)`
  - `idempotency_cleanup_interval_seconds: int = Field(default=3600, ge=59)`
- **L603** `intconst` n -> n+1 — `0320e36b0df6`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `idempotency_cleanup_batch_size: int = Field(default=501, ge=1, le=10000)`
- **L603** `intconst` n -> n-1 — `cdd4c1614975`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `idempotency_cleanup_batch_size: int = Field(default=499, ge=1, le=10000)`
- **L603** `intconst` n -> n+1 — `11ee6e103a8b`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=2, le=10000)`
- **L603** `intconst` n -> n-1 — `cd440efaa497`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=0, le=10000)`
- **L603** `intconst` n -> n+1 — `dbdd56822b5c`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=10001)`
- **L603** `intconst` n -> n-1 — `2796b2d20efb`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `idempotency_cleanup_batch_size: int = Field(default=500, ge=1, le=9999)`
- **L604** `intconst` n -> n+1 — `422bfb4f798a`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `idempotency_cleanup_max_batches: int = Field(default=11, ge=1, le=100)`
- **L604** `intconst` n -> n-1 — `8080366ef878`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `idempotency_cleanup_max_batches: int = Field(default=9, ge=1, le=100)`
- **L604** `intconst` n -> n+1 — `48d8da589513`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=2, le=100)`
- **L604** `intconst` n -> n-1 — `57c65138e300`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=0, le=100)`
- **L604** `intconst` n -> n+1 — `9a3c745ef602`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=101)`
- **L604** `intconst` n -> n-1 — `b5f309ac91a5`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `idempotency_cleanup_max_batches: int = Field(default=10, ge=1, le=99)`
- **L608** `intconst` n -> n+1 — `b8e214aaa628`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=1, le=100000)`
  - `idempotency_max_open_records_per_actor: int = Field(default=1001, ge=1, le=100000)`
- **L608** `intconst` n -> n-1 — `0fa6a59d7eb2`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=1, le=100000)`
  - `idempotency_max_open_records_per_actor: int = Field(default=999, ge=1, le=100000)`
- **L608** `intconst` n -> n+1 — `805d4bd7ae1a`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=1, le=100000)`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=2, le=100000)`
- **L608** `intconst` n -> n-1 — `50d45a955414`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=1, le=100000)`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=0, le=100000)`
- **L608** `intconst` n -> n+1 — `3eb9769de960`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=1, le=100000)`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=1, le=100001)`
- **L608** `intconst` n -> n-1 — `f2e22b5926ca`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=1, le=100000)`
  - `idempotency_max_open_records_per_actor: int = Field(default=1000, ge=1, le=99999)`
- **L622** `intconst` n -> n+1 — `dac6d808e612`
  - `legacy_repair_interval_seconds: int = Field(default=3600, ge=60)`
  - `legacy_repair_interval_seconds: int = Field(default=3601, ge=60)`
- **L622** `intconst` n -> n-1 — `f1b1eb4d90ca`
  - `legacy_repair_interval_seconds: int = Field(default=3600, ge=60)`
  - `legacy_repair_interval_seconds: int = Field(default=3599, ge=60)`
- **L622** `intconst` n -> n+1 — `55ebf28b1e0a`
  - `legacy_repair_interval_seconds: int = Field(default=3600, ge=60)`
  - `legacy_repair_interval_seconds: int = Field(default=3600, ge=61)`
- **L622** `intconst` n -> n-1 — `d189ea7c2a94`
  - `legacy_repair_interval_seconds: int = Field(default=3600, ge=60)`
  - `legacy_repair_interval_seconds: int = Field(default=3600, ge=59)`
- **L623** `intconst` n -> n+1 — `863fd6ba3828`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=500)`
  - `legacy_repair_farm_batch_size: int = Field(default=26, ge=1, le=500)`
- **L623** `intconst` n -> n-1 — `985df16554f6`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=500)`
  - `legacy_repair_farm_batch_size: int = Field(default=24, ge=1, le=500)`
- **L623** `intconst` n -> n+1 — `b630adfb75d1`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=500)`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=2, le=500)`
- **L623** `intconst` n -> n-1 — `c5f2f654bf6e`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=500)`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=0, le=500)`
- **L623** `intconst` n -> n+1 — `ce4474e8bf67`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=500)`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=501)`
- **L623** `intconst` n -> n-1 — `3b23121586e0`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=500)`
  - `legacy_repair_farm_batch_size: int = Field(default=25, ge=1, le=499)`
- **L624** `intconst` n -> n+1 — `7b6b9643f27d`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `legacy_repair_task_batch_size: int = Field(default=501, ge=1, le=10000)`
- **L624** `intconst` n -> n-1 — `70e8d6850356`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `legacy_repair_task_batch_size: int = Field(default=499, ge=1, le=10000)`
- **L624** `intconst` n -> n+1 — `57dd16645066`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=2, le=10000)`
- **L624** `intconst` n -> n-1 — `fd0d14098459`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=0, le=10000)`
- **L624** `intconst` n -> n+1 — `a5ea05c8b689`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=10001)`
- **L624** `intconst` n -> n-1 — `aea2ba86b2da`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `legacy_repair_task_batch_size: int = Field(default=500, ge=1, le=9999)`
- **L625** `intconst` n -> n+1 — `499f7656c598`
  - `legacy_repair_max_batches: int = Field(default=10, ge=1, le=100)`
  - `legacy_repair_max_batches: int = Field(default=11, ge=1, le=100)`
- **L625** `intconst` n -> n-1 — `d19a1faa7dfd`
  - `legacy_repair_max_batches: int = Field(default=10, ge=1, le=100)`
  - `legacy_repair_max_batches: int = Field(default=9, ge=1, le=100)`
- **L625** `intconst` n -> n+1 — `1a76ac0f92a2`
  - `legacy_repair_max_batches: int = Field(default=10, ge=1, le=100)`
  - `legacy_repair_max_batches: int = Field(default=10, ge=2, le=100)`
- **L625** `intconst` n -> n-1 — `866c3ba284f6`
  - `legacy_repair_max_batches: int = Field(default=10, ge=1, le=100)`
  - `legacy_repair_max_batches: int = Field(default=10, ge=0, le=100)`
- **L625** `intconst` n -> n+1 — `3f6c47003bae`
  - `legacy_repair_max_batches: int = Field(default=10, ge=1, le=100)`
  - `legacy_repair_max_batches: int = Field(default=10, ge=1, le=101)`
- **L625** `intconst` n -> n-1 — `236848e0d5df`
  - `legacy_repair_max_batches: int = Field(default=10, ge=1, le=100)`
  - `legacy_repair_max_batches: int = Field(default=10, ge=1, le=99)`
- **L630** `intconst` n -> n+1 — `9928f84eb4e6`
  - `inactive_animal_task_cleanup_interval_seconds: int = Field(default=60, ge=10)`
  - `inactive_animal_task_cleanup_interval_seconds: int = Field(default=61, ge=10)`
- **L630** `intconst` n -> n-1 — `da0caec9a655`
  - `inactive_animal_task_cleanup_interval_seconds: int = Field(default=60, ge=10)`
  - `inactive_animal_task_cleanup_interval_seconds: int = Field(default=59, ge=10)`
- **L630** `intconst` n -> n+1 — `ef77b8a81669`
  - `inactive_animal_task_cleanup_interval_seconds: int = Field(default=60, ge=10)`
  - `inactive_animal_task_cleanup_interval_seconds: int = Field(default=60, ge=11)`
- **L630** `intconst` n -> n-1 — `f44bfe583441`
  - `inactive_animal_task_cleanup_interval_seconds: int = Field(default=60, ge=10)`
  - `inactive_animal_task_cleanup_interval_seconds: int = Field(default=60, ge=9)`
- **L631** `intconst` n -> n+1 — `08c2df94907a`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=501, ge=1, le=10000)`
- **L631** `intconst` n -> n-1 — `8ed5f0714258`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=499, ge=1, le=10000)`
- **L631** `intconst` n -> n+1 — `35fda5e7f66c`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=2, le=10000)`
- **L631** `intconst` n -> n-1 — `8983913acb31`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=0, le=10000)`
- **L631** `intconst` n -> n+1 — `bdcc8a90dbe0`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=10001)`
- **L631** `intconst` n -> n-1 — `4907d4b4815c`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `inactive_animal_task_cleanup_batch_size: int = Field(default=500, ge=1, le=9999)`
- **L632** `intconst` n -> n+1 — `83d8f8a3238f`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=11, ge=1, le=100)`
- **L632** `intconst` n -> n-1 — `c7c7b4ec17f7`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=9, ge=1, le=100)`
- **L632** `intconst` n -> n+1 — `c5119e1c5798`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=2, le=100)`
- **L632** `intconst` n -> n-1 — `7e89dac69fcd`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=0, le=100)`
- **L632** `intconst` n -> n+1 — `4f27b735faf2`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=101)`
- **L632** `intconst` n -> n-1 — `9dcc79e508f8`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `inactive_animal_task_cleanup_max_batches: int = Field(default=10, ge=1, le=99)`
- **L636** `intconst` n -> n+1 — `b4ab33860234`
  - `deleted_membership_cleanup_interval_seconds: int = Field(default=60, ge=10)`
  - `deleted_membership_cleanup_interval_seconds: int = Field(default=61, ge=10)`
- **L636** `intconst` n -> n-1 — `1cab7b1cc93a`
  - `deleted_membership_cleanup_interval_seconds: int = Field(default=60, ge=10)`
  - `deleted_membership_cleanup_interval_seconds: int = Field(default=59, ge=10)`
- **L636** `intconst` n -> n+1 — `e1d8a0180029`
  - `deleted_membership_cleanup_interval_seconds: int = Field(default=60, ge=10)`
  - `deleted_membership_cleanup_interval_seconds: int = Field(default=60, ge=11)`
- **L636** `intconst` n -> n-1 — `303d8f2f8b6f`
  - `deleted_membership_cleanup_interval_seconds: int = Field(default=60, ge=10)`
  - `deleted_membership_cleanup_interval_seconds: int = Field(default=60, ge=9)`
- **L637** `intconst` n -> n+1 — `8b1b546069e6`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `deleted_membership_cleanup_batch_size: int = Field(default=501, ge=1, le=10000)`
- **L637** `intconst` n -> n-1 — `d18b40ab32c5`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `deleted_membership_cleanup_batch_size: int = Field(default=499, ge=1, le=10000)`
- **L637** `intconst` n -> n+1 — `6e6d7bda4570`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=2, le=10000)`
- **L637** `intconst` n -> n-1 — `8d3e285ba784`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=0, le=10000)`
- **L637** `intconst` n -> n+1 — `80c0756e3fe7`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=10001)`
- **L637** `intconst` n -> n-1 — `4c73c91b32ca`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `deleted_membership_cleanup_batch_size: int = Field(default=500, ge=1, le=9999)`
- **L638** `intconst` n -> n+1 — `2e809befcbe7`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `deleted_membership_cleanup_max_batches: int = Field(default=11, ge=1, le=100)`
- **L638** `intconst` n -> n-1 — `d9301c3bb7d2`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `deleted_membership_cleanup_max_batches: int = Field(default=9, ge=1, le=100)`
- **L638** `intconst` n -> n+1 — `1ec83c2cee07`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=2, le=100)`
- **L638** `intconst` n -> n-1 — `d6794a8b1f4f`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=0, le=100)`
- **L638** `intconst` n -> n+1 — `25034339604e`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=101)`
- **L638** `intconst` n -> n-1 — `5d360277544e`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `deleted_membership_cleanup_max_batches: int = Field(default=10, ge=1, le=99)`
- **L644** `intconst` n -> n+1 — `5700ea801623`
  - `cadence_materialization_interval_seconds: int = Field(default=300, ge=10)`
  - `cadence_materialization_interval_seconds: int = Field(default=301, ge=10)`
- **L644** `intconst` n -> n-1 — `a555a6a50a88`
  - `cadence_materialization_interval_seconds: int = Field(default=300, ge=10)`
  - `cadence_materialization_interval_seconds: int = Field(default=299, ge=10)`
- **L644** `intconst` n -> n+1 — `18e7e8ecbf51`
  - `cadence_materialization_interval_seconds: int = Field(default=300, ge=10)`
  - `cadence_materialization_interval_seconds: int = Field(default=300, ge=11)`
- **L644** `intconst` n -> n-1 — `15ff1394c2d3`
  - `cadence_materialization_interval_seconds: int = Field(default=300, ge=10)`
  - `cadence_materialization_interval_seconds: int = Field(default=300, ge=9)`
- **L645** `intconst` n -> n+1 — `39711581af45`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `cadence_materialization_farm_batch_size: int = Field(default=101, ge=1, le=1000)`
- **L645** `intconst` n -> n-1 — `466ca76935ec`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `cadence_materialization_farm_batch_size: int = Field(default=99, ge=1, le=1000)`
- **L645** `intconst` n -> n+1 — `0b0c3f48fef2`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=2, le=1000)`
- **L645** `intconst` n -> n-1 — `8ff78dd48712`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=0, le=1000)`
- **L645** `intconst` n -> n+1 — `b93657e9a78b`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=1001)`
- **L645** `intconst` n -> n-1 — `855cc8218520`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `cadence_materialization_farm_batch_size: int = Field(default=100, ge=1, le=999)`
- **L646** `intconst` n -> n+1 — `acd05bcf2c13`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=1, le=100)`
  - `cadence_materialization_max_batches: int = Field(default=11, ge=1, le=100)`
- **L646** `intconst` n -> n-1 — `53a6c2bc401f`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=1, le=100)`
  - `cadence_materialization_max_batches: int = Field(default=9, ge=1, le=100)`
- **L646** `intconst` n -> n+1 — `daf04520e16e`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=1, le=100)`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=2, le=100)`
- **L646** `intconst` n -> n-1 — `86f47d3984c9`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=1, le=100)`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=0, le=100)`
- **L646** `intconst` n -> n+1 — `be261c7c12a9`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=1, le=100)`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=1, le=101)`
- **L646** `intconst` n -> n-1 — `b5b2f66ed982`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=1, le=100)`
  - `cadence_materialization_max_batches: int = Field(default=10, ge=1, le=99)`
- **L651** `intconst` n -> n+1 — `e0507287474f`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1024, le=20971520)`
  - `max_request_body_bytes: int = Field(default=1048577, ge=1024, le=20971520)`
- **L651** `intconst` n -> n-1 — `baff82c01199`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1024, le=20971520)`
  - `max_request_body_bytes: int = Field(default=1048575, ge=1024, le=20971520)`
- **L651** `intconst` n -> n+1 — `f349193ee03f`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1024, le=20971520)`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1025, le=20971520)`
- **L651** `intconst` n -> n-1 — `02e022b26af8`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1024, le=20971520)`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1023, le=20971520)`
- **L651** `intconst` n -> n+1 — `eae46c631e97`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1024, le=20971520)`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1024, le=20971521)`
- **L651** `intconst` n -> n-1 — `710403be7647`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1024, le=20971520)`
  - `max_request_body_bytes: int = Field(default=1048576, ge=1024, le=20971519)`
- **L652** `intconst` n -> n+1 — `5459f8f6c251`
  - `max_request_target_bytes: int = Field(default=8192, ge=256, le=65536)`
  - `max_request_target_bytes: int = Field(default=8193, ge=256, le=65536)`
- **L652** `intconst` n -> n-1 — `a2c4a8604b07`
  - `max_request_target_bytes: int = Field(default=8192, ge=256, le=65536)`
  - `max_request_target_bytes: int = Field(default=8191, ge=256, le=65536)`
- **L652** `intconst` n -> n+1 — `8a5612872994`
  - `max_request_target_bytes: int = Field(default=8192, ge=256, le=65536)`
  - `max_request_target_bytes: int = Field(default=8192, ge=257, le=65536)`
- **L652** `intconst` n -> n-1 — `59882e0e812b`
  - `max_request_target_bytes: int = Field(default=8192, ge=256, le=65536)`
  - `max_request_target_bytes: int = Field(default=8192, ge=255, le=65536)`
- **L652** `intconst` n -> n+1 — `01cf0f1b6d19`
  - `max_request_target_bytes: int = Field(default=8192, ge=256, le=65536)`
  - `max_request_target_bytes: int = Field(default=8192, ge=256, le=65537)`
- **L652** `intconst` n -> n-1 — `1d75bf9a7c9f`
  - `max_request_target_bytes: int = Field(default=8192, ge=256, le=65536)`
  - `max_request_target_bytes: int = Field(default=8192, ge=256, le=65535)`
- **L682** `intconst` n -> n+1 — `3fa030345e42`
  - `jwt_issuer: str = Field(default='goatfarm-api', min_length=1, max_length=200)`
  - `jwt_issuer: str = Field(default='goatfarm-api', min_length=2, max_length=200)`
- **L682** `intconst` n -> n-1 — `f845921e6001`
  - `jwt_issuer: str = Field(default='goatfarm-api', min_length=1, max_length=200)`
  - `jwt_issuer: str = Field(default='goatfarm-api', min_length=0, max_length=200)`
- **L682** `intconst` n -> n+1 — `2f047a06536a`
  - `jwt_issuer: str = Field(default='goatfarm-api', min_length=1, max_length=200)`
  - `jwt_issuer: str = Field(default='goatfarm-api', min_length=1, max_length=201)`
- **L682** `intconst` n -> n-1 — `497c7bc082a3`
  - `jwt_issuer: str = Field(default='goatfarm-api', min_length=1, max_length=200)`
  - `jwt_issuer: str = Field(default='goatfarm-api', min_length=1, max_length=199)`
- **L683** `intconst` n -> n+1 — `bee59cbfa5c6`
  - `jwt_audience: str = Field(default='goatfarm-web', min_length=1, max_length=200)`
  - `jwt_audience: str = Field(default='goatfarm-web', min_length=2, max_length=200)`
- **L683** `intconst` n -> n-1 — `36c2fd9d4867`
  - `jwt_audience: str = Field(default='goatfarm-web', min_length=1, max_length=200)`
  - `jwt_audience: str = Field(default='goatfarm-web', min_length=0, max_length=200)`
- **L683** `intconst` n -> n+1 — `30ed95575bc4`
  - `jwt_audience: str = Field(default='goatfarm-web', min_length=1, max_length=200)`
  - `jwt_audience: str = Field(default='goatfarm-web', min_length=1, max_length=201)`
- **L683** `intconst` n -> n-1 — `1043d6225c74`
  - `jwt_audience: str = Field(default='goatfarm-web', min_length=1, max_length=200)`
  - `jwt_audience: str = Field(default='goatfarm-web', min_length=1, max_length=199)`
- **L684** `intconst` n -> n+1 — `e0fb37b38f41`
  - `access_token_ttl_seconds: int = Field(default=30 * 60, ge=1, le=60 * 60 * 24)`
  - `access_token_ttl_seconds: int = Field(default=30 * 60, ge=2, le=60 * 60 * 24)`
- **L684** `intconst` n -> n-1 — `4c7ef9ee8534`
  - `access_token_ttl_seconds: int = Field(default=30 * 60, ge=1, le=60 * 60 * 24)`
  - `access_token_ttl_seconds: int = Field(default=30 * 60, ge=0, le=60 * 60 * 24)`
- **L684** `intconst` n -> n+1 — `7ec83c25d8f7`
  - `access_token_ttl_seconds: int = Field(default=30 * 60, ge=1, le=60 * 60 * 24)`
  - `access_token_ttl_seconds: int = Field(default=31 * 60, ge=1, le=60 * 60 * 24)`
- **L684** `intconst` n -> n-1 — `16c767e8ab38`
  - `access_token_ttl_seconds: int = Field(default=30 * 60, ge=1, le=60 * 60 * 24)`
  - `access_token_ttl_seconds: int = Field(default=29 * 60, ge=1, le=60 * 60 * 24)`
- **L684** `intconst` n -> n+1 — `a9c808de5df3`
  - `access_token_ttl_seconds: int = Field(default=30 * 60, ge=1, le=60 * 60 * 24)`
  - `access_token_ttl_seconds: int = Field(default=30 * 61, ge=1, le=60 * 60 * 24)`
- **L684** `intconst` n -> n-1 — `c8bbbb07d18d`
  - `access_token_ttl_seconds: int = Field(default=30 * 60, ge=1, le=60 * 60 * 24)`
  - `access_token_ttl_seconds: int = Field(default=30 * 59, ge=1, le=60 * 60 * 24)`
- **L685** `intconst` n -> n+1 — `033fcae06f23`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=2, le=60 * 60 * 24 * 365)`
- **L685** `intconst` n -> n-1 — `047f7e391b2f`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=0, le=60 * 60 * 24 * 365)`
- **L685** `binop` Mult -> Div — `f4792875c467`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 / 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
- **L685** `intconst` n -> n+1 — `1f5ab250e497`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 15, ge=1, le=60 * 60 * 24 * 365)`
- **L685** `intconst` n -> n-1 — `60c446fb1744`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 13, ge=1, le=60 * 60 * 24 * 365)`
- **L685** `intconst` n -> n+1 — `31b722970667`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 366)`
- **L685** `intconst` n -> n-1 — `7b012bae169e`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 364)`
- **L685** `binop` Mult -> Div — `b9e9f78ec3c0`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 / 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
- **L685** `intconst` n -> n+1 — `b3256673b85d`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 25 * 14, ge=1, le=60 * 60 * 24 * 365)`
- **L685** `intconst` n -> n-1 — `3a7364fbce99`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 23 * 14, ge=1, le=60 * 60 * 24 * 365)`
- **L685** `intconst` n -> n+1 — `fb75f9abe9ba`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=61 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
- **L685** `intconst` n -> n-1 — `76e9faa0e88c`
  - `refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
  - `refresh_token_ttl_seconds: int = Field(default=59 * 60 * 24 * 14, ge=1, le=60 * 60 * 24 * 365)`
- **L688** `intconst` n -> n+1 — `d0ce9134aeee`
  - `refresh_session_cleanup_interval_seconds: int = Field(default=3600, ge=60)`
  - `refresh_session_cleanup_interval_seconds: int = Field(default=3601, ge=60)`
- **L688** `intconst` n -> n-1 — `fecbb8fc75f3`
  - `refresh_session_cleanup_interval_seconds: int = Field(default=3600, ge=60)`
  - `refresh_session_cleanup_interval_seconds: int = Field(default=3599, ge=60)`
- **L688** `intconst` n -> n+1 — `0a4e518f0d78`
  - `refresh_session_cleanup_interval_seconds: int = Field(default=3600, ge=60)`
  - `refresh_session_cleanup_interval_seconds: int = Field(default=3600, ge=61)`
- **L688** `intconst` n -> n-1 — `106cc87af460`
  - `refresh_session_cleanup_interval_seconds: int = Field(default=3600, ge=60)`
  - `refresh_session_cleanup_interval_seconds: int = Field(default=3600, ge=59)`
- **L689** `intconst` n -> n+1 — `eb60eb50b1f7`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `refresh_session_cleanup_batch_size: int = Field(default=501, ge=1, le=10000)`
- **L689** `intconst` n -> n-1 — `3884916b11d0`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `refresh_session_cleanup_batch_size: int = Field(default=499, ge=1, le=10000)`
- **L689** `intconst` n -> n+1 — `366e34496e2f`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=2, le=10000)`
- **L689** `intconst` n -> n-1 — `0830dac51fb2`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=0, le=10000)`
- **L689** `intconst` n -> n+1 — `15387b85216f`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=10001)`
- **L689** `intconst` n -> n-1 — `24820b4cbddb`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=10000)`
  - `refresh_session_cleanup_batch_size: int = Field(default=500, ge=1, le=9999)`
- **L690** `intconst` n -> n+1 — `a547828d5f4c`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `refresh_session_cleanup_max_batches: int = Field(default=11, ge=1, le=100)`
- **L690** `intconst` n -> n-1 — `1433eb6d1ddf`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `refresh_session_cleanup_max_batches: int = Field(default=9, ge=1, le=100)`
- **L690** `intconst` n -> n+1 — `4d95baf7a9e9`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=2, le=100)`
- **L690** `intconst` n -> n-1 — `8f88256b0c84`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=0, le=100)`
- **L690** `intconst` n -> n+1 — `15fb7dfcbcb7`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=101)`
- **L690** `intconst` n -> n-1 — `00427bb30f33`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=100)`
  - `refresh_session_cleanup_max_batches: int = Field(default=10, ge=1, le=99)`
- **L695** `intconst` n -> n+1 — `3606035f5a3e`
  - `refresh_max_families_per_user: int = Field(default=10, ge=1, le=50)`
  - `refresh_max_families_per_user: int = Field(default=11, ge=1, le=50)`
- **L695** `intconst` n -> n-1 — `aae4f34eab62`
  - `refresh_max_families_per_user: int = Field(default=10, ge=1, le=50)`
  - `refresh_max_families_per_user: int = Field(default=9, ge=1, le=50)`
- **L695** `intconst` n -> n+1 — `a20769ada35d`
  - `refresh_max_families_per_user: int = Field(default=10, ge=1, le=50)`
  - `refresh_max_families_per_user: int = Field(default=10, ge=2, le=50)`
- **L695** `intconst` n -> n-1 — `3eec3364164a`
  - `refresh_max_families_per_user: int = Field(default=10, ge=1, le=50)`
  - `refresh_max_families_per_user: int = Field(default=10, ge=0, le=50)`
- **L695** `intconst` n -> n+1 — `427d8e0224b8`
  - `refresh_max_families_per_user: int = Field(default=10, ge=1, le=50)`
  - `refresh_max_families_per_user: int = Field(default=10, ge=1, le=51)`
- **L695** `intconst` n -> n-1 — `cbb45ad1f5ac`
  - `refresh_max_families_per_user: int = Field(default=10, ge=1, le=50)`
  - `refresh_max_families_per_user: int = Field(default=10, ge=1, le=49)`
- **L696** `intconst` n -> n+1 — `7bba19b5d930`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4096)`
  - `refresh_max_sessions_per_family: int = Field(default=1025, ge=2, le=4096)`
- **L696** `intconst` n -> n-1 — `9f9d9fece849`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4096)`
  - `refresh_max_sessions_per_family: int = Field(default=1023, ge=2, le=4096)`
- **L696** `intconst` n -> n+1 — `ca434081bb41`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4096)`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=3, le=4096)`
- **L696** `intconst` n -> n-1 — `d93d59745051`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4096)`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=1, le=4096)`
- **L696** `intconst` n -> n+1 — `ede82f831018`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4096)`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4097)`
- **L696** `intconst` n -> n-1 — `5aa11b21636f`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4096)`
  - `refresh_max_sessions_per_family: int = Field(default=1024, ge=2, le=4095)`
- **L699** `intconst` n -> n+1 — `ae9a0ecbb6a2`
  - `refresh_reuse_grace_seconds: int = Field(default=3, ge=0, le=30)`
  - `refresh_reuse_grace_seconds: int = Field(default=4, ge=0, le=30)`
- **L699** `intconst` n -> n-1 — `88cd00365bbb`
  - `refresh_reuse_grace_seconds: int = Field(default=3, ge=0, le=30)`
  - `refresh_reuse_grace_seconds: int = Field(default=2, ge=0, le=30)`
- **L699** `intconst` n -> n+1 — `d8dc927c87ec`
  - `refresh_reuse_grace_seconds: int = Field(default=3, ge=0, le=30)`
  - `refresh_reuse_grace_seconds: int = Field(default=3, ge=1, le=30)`
- **L699** `intconst` n -> n+1 — `e863cca4bc03`
  - `refresh_reuse_grace_seconds: int = Field(default=3, ge=0, le=30)`
  - `refresh_reuse_grace_seconds: int = Field(default=3, ge=0, le=31)`
- **L699** `intconst` n -> n-1 — `6d8e2534f781`
  - `refresh_reuse_grace_seconds: int = Field(default=3, ge=0, le=30)`
  - `refresh_reuse_grace_seconds: int = Field(default=3, ge=0, le=29)`
- **L703** `intconst` n -> n+1 — `b370c81e2156`
  - `argon2_time_cost: int = Field(default=3, ge=1, le=6)`
  - `argon2_time_cost: int = Field(default=4, ge=1, le=6)`
- **L703** `intconst` n -> n-1 — `384e8cc6f77e`
  - `argon2_time_cost: int = Field(default=3, ge=1, le=6)`
  - `argon2_time_cost: int = Field(default=2, ge=1, le=6)`
- **L703** `intconst` n -> n+1 — `b8eb2e56ded8`
  - `argon2_time_cost: int = Field(default=3, ge=1, le=6)`
  - `argon2_time_cost: int = Field(default=3, ge=2, le=6)`
- **L703** `intconst` n -> n-1 — `9a017692fc14`
  - `argon2_time_cost: int = Field(default=3, ge=1, le=6)`
  - `argon2_time_cost: int = Field(default=3, ge=0, le=6)`
- **L703** `intconst` n -> n+1 — `95090b6e5b1d`
  - `argon2_time_cost: int = Field(default=3, ge=1, le=6)`
  - `argon2_time_cost: int = Field(default=3, ge=1, le=7)`
- **L703** `intconst` n -> n-1 — `18dcb42a29b8`
  - `argon2_time_cost: int = Field(default=3, ge=1, le=6)`
  - `argon2_time_cost: int = Field(default=3, ge=1, le=5)`
- **L704** `intconst` n -> n+1 — `60748c8648a5`
  - `argon2_memory_cost: int = Field(default=65536, ge=8, le=131072)`
  - `argon2_memory_cost: int = Field(default=65537, ge=8, le=131072)`
- **L704** `intconst` n -> n-1 — `6300db28dab9`
  - `argon2_memory_cost: int = Field(default=65536, ge=8, le=131072)`
  - `argon2_memory_cost: int = Field(default=65535, ge=8, le=131072)`
- **L704** `intconst` n -> n+1 — `222feff7b0c9`
  - `argon2_memory_cost: int = Field(default=65536, ge=8, le=131072)`
  - `argon2_memory_cost: int = Field(default=65536, ge=9, le=131072)`
- **L704** `intconst` n -> n-1 — `78a91a5a994f`
  - `argon2_memory_cost: int = Field(default=65536, ge=8, le=131072)`
  - `argon2_memory_cost: int = Field(default=65536, ge=7, le=131072)`
- **L704** `intconst` n -> n+1 — `60eb9a80bf2d`
  - `argon2_memory_cost: int = Field(default=65536, ge=8, le=131072)`
  - `argon2_memory_cost: int = Field(default=65536, ge=8, le=131073)`
- **L704** `intconst` n -> n-1 — `ec785c2eec28`
  - `argon2_memory_cost: int = Field(default=65536, ge=8, le=131072)`
  - `argon2_memory_cost: int = Field(default=65536, ge=8, le=131071)`
- **L705** `intconst` n -> n+1 — `b9b8eeb2bbae`
  - `argon2_parallelism: int = Field(default=4, ge=1, le=8)`
  - `argon2_parallelism: int = Field(default=5, ge=1, le=8)`
- **L705** `intconst` n -> n-1 — `68177f7349c4`
  - `argon2_parallelism: int = Field(default=4, ge=1, le=8)`
  - `argon2_parallelism: int = Field(default=3, ge=1, le=8)`
- **L705** `intconst` n -> n+1 — `fa1acce81bff`
  - `argon2_parallelism: int = Field(default=4, ge=1, le=8)`
  - `argon2_parallelism: int = Field(default=4, ge=2, le=8)`
- **L705** `intconst` n -> n-1 — `971721acbbf3`
  - `argon2_parallelism: int = Field(default=4, ge=1, le=8)`
  - `argon2_parallelism: int = Field(default=4, ge=0, le=8)`
- **L705** `intconst` n -> n+1 — `1bde735a986e`
  - `argon2_parallelism: int = Field(default=4, ge=1, le=8)`
  - `argon2_parallelism: int = Field(default=4, ge=1, le=9)`
- **L705** `intconst` n -> n-1 — `dee65fd712c1`
  - `argon2_parallelism: int = Field(default=4, ge=1, le=8)`
  - `argon2_parallelism: int = Field(default=4, ge=1, le=7)`
- **L706** `intconst` n -> n+1 — `758e12548bd0`
  - `argon2_hash_len: int = Field(default=32, ge=16, le=64)`
  - `argon2_hash_len: int = Field(default=33, ge=16, le=64)`
- **L706** `intconst` n -> n-1 — `1a526220ac58`
  - `argon2_hash_len: int = Field(default=32, ge=16, le=64)`
  - `argon2_hash_len: int = Field(default=31, ge=16, le=64)`
- **L706** `intconst` n -> n+1 — `be5c07cefbc4`
  - `argon2_hash_len: int = Field(default=32, ge=16, le=64)`
  - `argon2_hash_len: int = Field(default=32, ge=17, le=64)`
- **L706** `intconst` n -> n-1 — `3357f666ffc8`
  - `argon2_hash_len: int = Field(default=32, ge=16, le=64)`
  - `argon2_hash_len: int = Field(default=32, ge=15, le=64)`
- **L706** `intconst` n -> n+1 — `75ce169bcbaa`
  - `argon2_hash_len: int = Field(default=32, ge=16, le=64)`
  - `argon2_hash_len: int = Field(default=32, ge=16, le=65)`
- **L706** `intconst` n -> n-1 — `a5b335133558`
  - `argon2_hash_len: int = Field(default=32, ge=16, le=64)`
  - `argon2_hash_len: int = Field(default=32, ge=16, le=63)`
- **L710** `intconst` n -> n+1 — `afdbabc6c76d`
  - `argon2_worker_threads: int = Field(default=2, ge=1, le=4)`
  - `argon2_worker_threads: int = Field(default=3, ge=1, le=4)`
- **L710** `intconst` n -> n-1 — `399e27f2930f`
  - `argon2_worker_threads: int = Field(default=2, ge=1, le=4)`
  - `argon2_worker_threads: int = Field(default=1, ge=1, le=4)`
- **L710** `intconst` n -> n+1 — `8e07e42f3cfc`
  - `argon2_worker_threads: int = Field(default=2, ge=1, le=4)`
  - `argon2_worker_threads: int = Field(default=2, ge=2, le=4)`
- **L710** `intconst` n -> n-1 — `c5019b8ec002`
  - `argon2_worker_threads: int = Field(default=2, ge=1, le=4)`
  - `argon2_worker_threads: int = Field(default=2, ge=0, le=4)`
- **L710** `intconst` n -> n+1 — `47ccec5972ab`
  - `argon2_worker_threads: int = Field(default=2, ge=1, le=4)`
  - `argon2_worker_threads: int = Field(default=2, ge=1, le=5)`
- **L710** `intconst` n -> n-1 — `eb48549baa84`
  - `argon2_worker_threads: int = Field(default=2, ge=1, le=4)`
  - `argon2_worker_threads: int = Field(default=2, ge=1, le=3)`
- **L716** `intconst` n -> n+1 — `3adbfd088a1d`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50000, ge=0, le=10000000)`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50001, ge=0, le=10000000)`
- **L716** `intconst` n -> n-1 — `68a278851d0a`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50000, ge=0, le=10000000)`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=49999, ge=0, le=10000000)`
- **L716** `intconst` n -> n+1 — `e305d0115648`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50000, ge=0, le=10000000)`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50000, ge=1, le=10000000)`
- **L716** `intconst` n -> n+1 — `c59a774d5b43`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50000, ge=0, le=10000000)`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50000, ge=0, le=10000001)`
- **L716** `intconst` n -> n-1 — `6cef67e064d4`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50000, ge=0, le=10000000)`
  - `rejected_login_pbkdf2_work_budget: int = Field(default=50000, ge=0, le=9999999)`
- **L728** `boolconst` False -> True — `f6351a4b45ed`
  - `cookie_secure: bool = False`
  - `cookie_secure: bool = True`
- **L730** `intconst` n -> n+1 — `1ea98f8d8864`
  - `min_password_length: int = Field(default=8, ge=1, le=128)`
  - `min_password_length: int = Field(default=9, ge=1, le=128)`
- **L730** `intconst` n -> n-1 — `ebd3bec2d405`
  - `min_password_length: int = Field(default=8, ge=1, le=128)`
  - `min_password_length: int = Field(default=7, ge=1, le=128)`
- **L730** `intconst` n -> n+1 — `342629ca2ee9`
  - `min_password_length: int = Field(default=8, ge=1, le=128)`
  - `min_password_length: int = Field(default=8, ge=2, le=128)`
- **L730** `intconst` n -> n-1 — `d2fd8408c028`
  - `min_password_length: int = Field(default=8, ge=1, le=128)`
  - `min_password_length: int = Field(default=8, ge=0, le=128)`
- **L730** `intconst` n -> n+1 — `38a2f0ea4f7a`
  - `min_password_length: int = Field(default=8, ge=1, le=128)`
  - `min_password_length: int = Field(default=8, ge=1, le=129)`
- **L730** `intconst` n -> n-1 — `cc6be7df576d`
  - `min_password_length: int = Field(default=8, ge=1, le=128)`
  - `min_password_length: int = Field(default=8, ge=1, le=127)`
- **L737** `boolconst` True -> False — `8bce11e49377`
  - `auth_rate_limit_enabled: bool = True`
  - `auth_rate_limit_enabled: bool = False`
- **L738** `intconst` n -> n+1 — `798fde59c296`
  - `auth_rate_limit_max_attempts: int = Field(default=10, ge=1)`
  - `auth_rate_limit_max_attempts: int = Field(default=11, ge=1)`
- **L738** `intconst` n -> n-1 — `f4b091f1f5b3`
  - `auth_rate_limit_max_attempts: int = Field(default=10, ge=1)`
  - `auth_rate_limit_max_attempts: int = Field(default=9, ge=1)`
- **L738** `intconst` n -> n+1 — `cc024e84ae3d`
  - `auth_rate_limit_max_attempts: int = Field(default=10, ge=1)`
  - `auth_rate_limit_max_attempts: int = Field(default=10, ge=2)`
- **L738** `intconst` n -> n-1 — `665a654b5302`
  - `auth_rate_limit_max_attempts: int = Field(default=10, ge=1)`
  - `auth_rate_limit_max_attempts: int = Field(default=10, ge=0)`
- **L739** `intconst` n -> n+1 — `8138654d05f2`
  - `auth_rate_limit_window_seconds: int = Field(default=300, ge=1)`
  - `auth_rate_limit_window_seconds: int = Field(default=301, ge=1)`
- **L739** `intconst` n -> n-1 — `1eb612e81153`
  - `auth_rate_limit_window_seconds: int = Field(default=300, ge=1)`
  - `auth_rate_limit_window_seconds: int = Field(default=299, ge=1)`
- **L739** `intconst` n -> n+1 — `9252a94616df`
  - `auth_rate_limit_window_seconds: int = Field(default=300, ge=1)`
  - `auth_rate_limit_window_seconds: int = Field(default=300, ge=2)`
- **L739** `intconst` n -> n-1 — `c849e83beadd`
  - `auth_rate_limit_window_seconds: int = Field(default=300, ge=1)`
  - `auth_rate_limit_window_seconds: int = Field(default=300, ge=0)`
- **L751** `boolconst` True -> False — `c3ec14660194`
  - `metrics_enabled: bool = True`
  - `metrics_enabled: bool = False`
- **L762** `intconst` n -> n+1 — `1ea7b4d8c5d7`
  - `max_farms_per_user: int = Field(default=10, ge=1)`
  - `max_farms_per_user: int = Field(default=11, ge=1)`
- **L762** `intconst` n -> n-1 — `1d8f334878bc`
  - `max_farms_per_user: int = Field(default=10, ge=1)`
  - `max_farms_per_user: int = Field(default=9, ge=1)`
- **L762** `intconst` n -> n+1 — `d3c8e2e28b09`
  - `max_farms_per_user: int = Field(default=10, ge=1)`
  - `max_farms_per_user: int = Field(default=10, ge=2)`
- **L762** `intconst` n -> n-1 — `15a24b3404b1`
  - `max_farms_per_user: int = Field(default=10, ge=1)`
  - `max_farms_per_user: int = Field(default=10, ge=0)`
- **L766** `intconst` n -> n+1 — `2c98bb3a18f7`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=1, le=10000)`
  - `max_account_affiliations_per_response: int = Field(default=501, ge=1, le=10000)`
- **L766** `intconst` n -> n-1 — `c9ef00024e49`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=1, le=10000)`
  - `max_account_affiliations_per_response: int = Field(default=499, ge=1, le=10000)`
- **L766** `intconst` n -> n+1 — `157c26df2685`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=1, le=10000)`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=2, le=10000)`
- **L766** `intconst` n -> n-1 — `76daa5fcc1c7`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=1, le=10000)`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=0, le=10000)`
- **L766** `intconst` n -> n+1 — `5ec455336f1a`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=1, le=10000)`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=1, le=10001)`
- **L766** `intconst` n -> n-1 — `fbb2bfdd6839`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=1, le=10000)`
  - `max_account_affiliations_per_response: int = Field(default=500, ge=1, le=9999)`
- **L772** `intconst` n -> n+1 — `63ecc6e71a17`
  - `max_team_members_per_farm: int = Field(default=200, ge=1, le=10000)`
  - `max_team_members_per_farm: int = Field(default=201, ge=1, le=10000)`
- **L772** `intconst` n -> n-1 — `540098efc32d`
  - `max_team_members_per_farm: int = Field(default=200, ge=1, le=10000)`
  - `max_team_members_per_farm: int = Field(default=199, ge=1, le=10000)`
- **L772** `intconst` n -> n+1 — `705dc1084c0a`
  - `max_team_members_per_farm: int = Field(default=200, ge=1, le=10000)`
  - `max_team_members_per_farm: int = Field(default=200, ge=2, le=10000)`
- **L772** `intconst` n -> n-1 — `8e9b88da118d`
  - `max_team_members_per_farm: int = Field(default=200, ge=1, le=10000)`
  - `max_team_members_per_farm: int = Field(default=200, ge=0, le=10000)`
- **L772** `intconst` n -> n+1 — `a6d6686fd6bb`
  - `max_team_members_per_farm: int = Field(default=200, ge=1, le=10000)`
  - `max_team_members_per_farm: int = Field(default=200, ge=1, le=10001)`
- **L772** `intconst` n -> n-1 — `f05ae7143b2d`
  - `max_team_members_per_farm: int = Field(default=200, ge=1, le=10000)`
  - `max_team_members_per_farm: int = Field(default=200, ge=1, le=9999)`
- **L773** `intconst` n -> n+1 — `90018e6107e8`
  - `max_roles_per_farm: int = Field(default=50, ge=1, le=1000)`
  - `max_roles_per_farm: int = Field(default=51, ge=1, le=1000)`
- **L773** `intconst` n -> n-1 — `a2480e3b8178`
  - `max_roles_per_farm: int = Field(default=50, ge=1, le=1000)`
  - `max_roles_per_farm: int = Field(default=49, ge=1, le=1000)`
- **L773** `intconst` n -> n+1 — `6613f9f87009`
  - `max_roles_per_farm: int = Field(default=50, ge=1, le=1000)`
  - `max_roles_per_farm: int = Field(default=50, ge=2, le=1000)`
- **L773** `intconst` n -> n-1 — `333f1e980551`
  - `max_roles_per_farm: int = Field(default=50, ge=1, le=1000)`
  - `max_roles_per_farm: int = Field(default=50, ge=0, le=1000)`
- **L773** `intconst` n -> n+1 — `2fb649b72169`
  - `max_roles_per_farm: int = Field(default=50, ge=1, le=1000)`
  - `max_roles_per_farm: int = Field(default=50, ge=1, le=1001)`
- **L773** `intconst` n -> n-1 — `57276c7b66ad`
  - `max_roles_per_farm: int = Field(default=50, ge=1, le=1000)`
  - `max_roles_per_farm: int = Field(default=50, ge=1, le=999)`
- **L774** `intconst` n -> n+1 — `63dda7f2f08b`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_simulation_scenarios_per_farm: int = Field(default=26, ge=1, le=500)`
- **L774** `intconst` n -> n-1 — `9346a715713a`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_simulation_scenarios_per_farm: int = Field(default=24, ge=1, le=500)`
- **L774** `intconst` n -> n+1 — `a465f171d69c`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=2, le=500)`
- **L774** `intconst` n -> n-1 — `4812ad231668`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=0, le=500)`
- **L774** `intconst` n -> n+1 — `3bb0a4fe52dd`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=501)`
- **L774** `intconst` n -> n-1 — `c7ff9f08d579`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_simulation_scenarios_per_farm: int = Field(default=25, ge=1, le=499)`
- **L775** `intconst` n -> n+1 — `95c09373a1f7`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_planner_plans_per_farm: int = Field(default=26, ge=1, le=500)`
- **L775** `intconst` n -> n-1 — `c003fd9399db`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_planner_plans_per_farm: int = Field(default=24, ge=1, le=500)`
- **L775** `intconst` n -> n+1 — `876b21157b49`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=2, le=500)`
- **L775** `intconst` n -> n-1 — `dd5bb708a52e`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=0, le=500)`
- **L775** `intconst` n -> n+1 — `40edfa2abbfd`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=1, le=501)`
- **L775** `intconst` n -> n-1 — `e036f8af9e50`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=1, le=500)`
  - `max_planner_plans_per_farm: int = Field(default=25, ge=1, le=499)`
- **L779** `intconst` n -> n+1 — `b588ee7e12a6`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=1, le=100000)`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5001, ge=1, le=100000)`
- **L779** `intconst` n -> n-1 — `caa8d755cb13`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=1, le=100000)`
  - `max_pending_manual_tasks_per_farm: int = Field(default=4999, ge=1, le=100000)`
- **L779** `intconst` n -> n+1 — `62bf91849053`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=1, le=100000)`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=2, le=100000)`
- **L779** `intconst` n -> n-1 — `8d105378e39d`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=1, le=100000)`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=0, le=100000)`
- **L779** `intconst` n -> n+1 — `e8c749abfc52`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=1, le=100000)`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=1, le=100001)`
- **L779** `intconst` n -> n-1 — `77ab0bb936d0`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=1, le=100000)`
  - `max_pending_manual_tasks_per_farm: int = Field(default=5000, ge=1, le=99999)`
- **L797** `intconst` n -> n+1 — `c89a9c359509`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=100)`
  - `screening_s3_prefix: str = Field(default='raw', min_length=2, max_length=100)`
- **L797** `intconst` n -> n-1 — `bd272ec2b8dc`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=100)`
  - `screening_s3_prefix: str = Field(default='raw', min_length=0, max_length=100)`
- **L797** `intconst` n -> n+1 — `b79177e87b16`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=100)`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=101)`
- **L797** `intconst` n -> n-1 — `24a345463bb9`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=100)`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=99)`
- **L801** `intconst` n -> n+1 — `d73b07bc0471`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=30)`
  - `screening_poll_interval_seconds: int = Field(default=301, ge=30)`
- **L801** `intconst` n -> n-1 — `924820b94274`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=30)`
  - `screening_poll_interval_seconds: int = Field(default=299, ge=30)`
- **L801** `intconst` n -> n+1 — `fc07c3d19035`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=30)`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=31)`
- **L801** `intconst` n -> n-1 — `7c3103017b0e`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=30)`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=29)`
- **L802** `intconst` n -> n+1 — `a6126bf40d1d`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=51, ge=1, le=1000)`
- **L802** `intconst` n -> n-1 — `0cb6d9095874`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=49, ge=1, le=1000)`
- **L802** `intconst` n -> n+1 — `7265c2df1bc3`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=2, le=1000)`
- **L802** `intconst` n -> n-1 — `ebfe183507be`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=0, le=1000)`
- **L802** `intconst` n -> n+1 — `c2434164916e`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1001)`
- **L802** `intconst` n -> n-1 — `4694a155e879`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=999)`
- **L810** `intconst` n -> n+1 — `b787c1e47420`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=401, ge=1, le=100000)`
- **L810** `intconst` n -> n-1 — `a7073b9540e6`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=399, ge=1, le=100000)`
- **L810** `intconst` n -> n+1 — `694a7ba6a527`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=2, le=100000)`
- **L810** `intconst` n -> n-1 — `c88e88c1cbfc`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=0, le=100000)`
- **L810** `intconst` n -> n+1 — `33303727f8c5`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100001)`
- **L810** `intconst` n -> n-1 — `2cd8d382a2aa`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=99999)`
- **L823** `intconst` n -> n+1 — `6188fd73feec`
  - `notifications_digest_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_digest_hour: int = Field(default=7, ge=0, le=23)`
- **L823** `intconst` n -> n-1 — `e3a27d322b59`
  - `notifications_digest_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_digest_hour: int = Field(default=5, ge=0, le=23)`
- **L823** `intconst` n -> n+1 — `73db210d5557`
  - `notifications_digest_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_digest_hour: int = Field(default=6, ge=1, le=23)`
- **L823** `intconst` n -> n+1 — `0cf256c01a37`
  - `notifications_digest_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_digest_hour: int = Field(default=6, ge=0, le=24)`
- **L823** `intconst` n -> n-1 — `83cfbfd8bc6c`
  - `notifications_digest_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_digest_hour: int = Field(default=6, ge=0, le=22)`
- **L824** `intconst` n -> n+1 — `1d1e41f6b0f9`
  - `notifications_digest_minute: int = Field(default=30, ge=0, le=59)`
  - `notifications_digest_minute: int = Field(default=31, ge=0, le=59)`
- **L824** `intconst` n -> n-1 — `b8167075b149`
  - `notifications_digest_minute: int = Field(default=30, ge=0, le=59)`
  - `notifications_digest_minute: int = Field(default=29, ge=0, le=59)`
- **L824** `intconst` n -> n+1 — `cb051771b863`
  - `notifications_digest_minute: int = Field(default=30, ge=0, le=59)`
  - `notifications_digest_minute: int = Field(default=30, ge=1, le=59)`
- **L824** `intconst` n -> n+1 — `6466b5293a57`
  - `notifications_digest_minute: int = Field(default=30, ge=0, le=59)`
  - `notifications_digest_minute: int = Field(default=30, ge=0, le=60)`
- **L824** `intconst` n -> n-1 — `421fd85d5289`
  - `notifications_digest_minute: int = Field(default=30, ge=0, le=59)`
  - `notifications_digest_minute: int = Field(default=30, ge=0, le=58)`
- **L826** `intconst` n -> n+1 — `6b3e6c02bd6d`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=1, le=1000)`
  - `notifications_farm_daily_cap: int = Field(default=51, ge=1, le=1000)`
- **L826** `intconst` n -> n-1 — `eb7355b42837`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=1, le=1000)`
  - `notifications_farm_daily_cap: int = Field(default=49, ge=1, le=1000)`
- **L826** `intconst` n -> n+1 — `1dcdaf659ed9`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=1, le=1000)`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=2, le=1000)`
- **L826** `intconst` n -> n-1 — `0d0fcbf2b91f`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=1, le=1000)`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=0, le=1000)`
- **L826** `intconst` n -> n+1 — `5041f60ac5f5`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=1, le=1000)`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=1, le=1001)`
- **L826** `intconst` n -> n-1 — `f93118abd56e`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=1, le=1000)`
  - `notifications_farm_daily_cap: int = Field(default=50, ge=1, le=999)`
- **L828** `intconst` n -> n+1 — `f8d7fbb0c445`
  - `notifications_quiet_start_hour: int = Field(default=21, ge=0, le=23)`
  - `notifications_quiet_start_hour: int = Field(default=22, ge=0, le=23)`
- **L828** `intconst` n -> n-1 — `73130b05b4a1`
  - `notifications_quiet_start_hour: int = Field(default=21, ge=0, le=23)`
  - `notifications_quiet_start_hour: int = Field(default=20, ge=0, le=23)`
- **L828** `intconst` n -> n+1 — `5a1b0d00d230`
  - `notifications_quiet_start_hour: int = Field(default=21, ge=0, le=23)`
  - `notifications_quiet_start_hour: int = Field(default=21, ge=1, le=23)`
- **L828** `intconst` n -> n+1 — `68466fec7508`
  - `notifications_quiet_start_hour: int = Field(default=21, ge=0, le=23)`
  - `notifications_quiet_start_hour: int = Field(default=21, ge=0, le=24)`
- **L828** `intconst` n -> n-1 — `5bcf479f5b17`
  - `notifications_quiet_start_hour: int = Field(default=21, ge=0, le=23)`
  - `notifications_quiet_start_hour: int = Field(default=21, ge=0, le=22)`
- **L829** `intconst` n -> n+1 — `a4ed23f2fa3c`
  - `notifications_quiet_end_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_quiet_end_hour: int = Field(default=7, ge=0, le=23)`
- **L829** `intconst` n -> n-1 — `27c383323d19`
  - `notifications_quiet_end_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_quiet_end_hour: int = Field(default=5, ge=0, le=23)`
- **L829** `intconst` n -> n+1 — `dca0eb48c100`
  - `notifications_quiet_end_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_quiet_end_hour: int = Field(default=6, ge=1, le=23)`
- **L829** `intconst` n -> n+1 — `e4f02e121f86`
  - `notifications_quiet_end_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_quiet_end_hour: int = Field(default=6, ge=0, le=24)`
- **L829** `intconst` n -> n-1 — `2da7d9ab4758`
  - `notifications_quiet_end_hour: int = Field(default=6, ge=0, le=23)`
  - `notifications_quiet_end_hour: int = Field(default=6, ge=0, le=22)`
- **L832** `intconst` n -> n+1 — `e69e2a9bcec4`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=1, le=5)`
  - `notifications_send_retry_attempts: int = Field(default=3, ge=1, le=5)`
- **L832** `intconst` n -> n-1 — `a234779dca86`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=1, le=5)`
  - `notifications_send_retry_attempts: int = Field(default=1, ge=1, le=5)`
- **L832** `intconst` n -> n+1 — `d129dbeff6a0`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=1, le=5)`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=2, le=5)`
- **L832** `intconst` n -> n-1 — `203e14ac5243`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=1, le=5)`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=0, le=5)`
- **L832** `intconst` n -> n+1 — `f3df18671bd8`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=1, le=5)`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=1, le=6)`
- **L832** `intconst` n -> n-1 — `7c94399f402e`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=1, le=5)`
  - `notifications_send_retry_attempts: int = Field(default=2, ge=1, le=4)`
- **L837** `intconst` n -> n+1 — `14baa34dec09`
  - `notifications_loop_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `notifications_loop_batch_size: int = Field(default=101, ge=1, le=1000)`
- **L837** `intconst` n -> n-1 — `59e2e4693ca9`
  - `notifications_loop_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `notifications_loop_batch_size: int = Field(default=99, ge=1, le=1000)`
- **L837** `intconst` n -> n+1 — `502618ca155d`
  - `notifications_loop_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `notifications_loop_batch_size: int = Field(default=100, ge=2, le=1000)`
- **L837** `intconst` n -> n-1 — `944b4b884d93`
  - `notifications_loop_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `notifications_loop_batch_size: int = Field(default=100, ge=0, le=1000)`
- **L837** `intconst` n -> n+1 — `1b799c732fb7`
  - `notifications_loop_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `notifications_loop_batch_size: int = Field(default=100, ge=1, le=1001)`
- **L837** `intconst` n -> n-1 — `6e82f23c0fc7`
  - `notifications_loop_batch_size: int = Field(default=100, ge=1, le=1000)`
  - `notifications_loop_batch_size: int = Field(default=100, ge=1, le=999)`
- **L843** `intconst` n -> n+1 — `c6074c813b1e`
  - `worker_pin_min_length: int = Field(default=4, ge=4, le=12)`
  - `worker_pin_min_length: int = Field(default=5, ge=4, le=12)`
- **L843** `intconst` n -> n-1 — `bf9b2ac441f6`
  - `worker_pin_min_length: int = Field(default=4, ge=4, le=12)`
  - `worker_pin_min_length: int = Field(default=4, ge=4, le=11)`
- **L844** `intconst` n -> n+1 — `e19a6cef8e6f`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=100)`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=11, ge=1, le=100)`
- **L844** `intconst` n -> n-1 — `fdfcde763fcd`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=100)`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=9, ge=1, le=100)`
- **L844** `intconst` n -> n+1 — `a18c0ad3db63`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=100)`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=2, le=100)`
- **L844** `intconst` n -> n-1 — `6d4b2d643b0c`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=100)`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=0, le=100)`
- **L844** `intconst` n -> n+1 — `8183f44fde12`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=100)`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=101)`
- **L844** `intconst` n -> n-1 — `84e1110d899d`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=100)`
  - `worker_pin_rate_limit_max_attempts: int = Field(default=10, ge=1, le=99)`
- **L845** `intconst` n -> n+1 — `2e6b451616c9`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3600)`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=301, ge=30, le=3600)`
- **L845** `intconst` n -> n-1 — `1dc13d033ba8`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3600)`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=299, ge=30, le=3600)`
- **L845** `intconst` n -> n+1 — `089b1cf3c55e`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3600)`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=31, le=3600)`
- **L845** `intconst` n -> n-1 — `f362fdb9fcf8`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3600)`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=29, le=3600)`
- **L845** `intconst` n -> n+1 — `4856bce28adf`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3600)`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3601)`
- **L845** `intconst` n -> n-1 — `89c5f5e5d958`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3600)`
  - `worker_pin_rate_limit_window_seconds: int = Field(default=300, ge=30, le=3599)`
- **L850** `boolconst` True -> False — `9e9e66182ce8`
  - `worker_roster_enabled: bool = True`
  - `worker_roster_enabled: bool = False`
- **L853** `intconst` n -> n+1 — `c4a8a8dc11fc`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1569, ge=256, le=4096)`
- **L853** `intconst` n -> n-1 — `261bd0b0c702`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1567, ge=256, le=4096)`
- **L853** `intconst` n -> n+1 — `3419b4b0ce09`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=257, le=4096)`
- **L853** `intconst` n -> n-1 — `7a9c763f8e6e`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=255, le=4096)`
- **L853** `intconst` n -> n+1 — `e25f066f78f3`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4097)`
- **L853** `intconst` n -> n-1 — `fdfb1d0534f6`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4095)`
- **L858** `boolconst` True -> False — `d9494541a01b`
  - `screening_crop_detection_enabled: bool = True`
  - `screening_crop_detection_enabled: bool = False`
- **L861** `intconst` n -> n+1 — `a03e259189e2`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=9, ge=1, le=20)`
- **L861** `intconst` n -> n-1 — `2b9d7d33daaf`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=7, ge=1, le=20)`
- **L861** `intconst` n -> n+1 — `07d6f4fbec23`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=8, ge=2, le=20)`
- **L861** `intconst` n -> n-1 — `de0bdd7bdf49`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=8, ge=0, le=20)`
- **L861** `intconst` n -> n+1 — `8ad29ba97b15`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=21)`
- **L861** `intconst` n -> n-1 — `f0b33b9115b1`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=19)`
- **L863** `intconst` n -> n+1 — `178328b03334`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=901, ge=60, le=86400)`
- **L863** `intconst` n -> n-1 — `512182a17c66`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=899, ge=60, le=86400)`
- **L863** `intconst` n -> n+1 — `b80a5752d806`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=61, le=86400)`
- **L863** `intconst` n -> n-1 — `643efd18c6a9`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=59, le=86400)`
- **L863** `intconst` n -> n+1 — `74a7a070edc4`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86401)`
- **L863** `intconst` n -> n-1 — `67651c8a8c68`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86399)`
- **L881** `intconst` n -> n+1 — `edee3b4783c2`
  - `screening_provider_rotation: list[ScreeningRotationProvider] = Field(default_factory=list, max_length=8)`
  - `screening_provider_rotation: list[ScreeningRotationProvider] = Field(default_factory=list, max_length=9)`
- **L881** `intconst` n -> n-1 — `7442cbed87ec`
  - `screening_provider_rotation: list[ScreeningRotationProvider] = Field(default_factory=list, max_length=8)`
  - `screening_provider_rotation: list[ScreeningRotationProvider] = Field(default_factory=list, max_length=7)`
- **L884** `intconst` n -> n+1 — `22abcfaad8eb`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=121, ge=10, le=600)`
- **L884** `intconst` n -> n-1 — `edc141b218c4`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=119, ge=10, le=600)`
- **L884** `intconst` n -> n+1 — `6c3ab24dc08c`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=11, le=600)`
- **L884** `intconst` n -> n-1 — `4f890b938c2e`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=9, le=600)`
- **L884** `intconst` n -> n+1 — `5d18c4401a01`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=601)`
- **L884** `intconst` n -> n-1 — `196a23e20f05`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=599)`
- **L890** `intconst` n -> n+1 — `b7cf75053047`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1801, ge=600, le=86400)`
- **L890** `intconst` n -> n-1 — `f83644cb3213`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1799, ge=600, le=86400)`
- **L890** `intconst` n -> n+1 — `d76992130069`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=601, le=86400)`
- **L890** `intconst` n -> n-1 — `f1f611498986`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=599, le=86400)`
- **L890** `intconst` n -> n+1 — `839a88683b5f`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86401)`
- **L890** `intconst` n -> n-1 — `7d29c4ed909e`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86399)`
- **L896** `intconst` n -> n+1 — `c2b6cf1475d3`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=901, ge=60, le=86400)`
- **L896** `intconst` n -> n-1 — `7013d99abb45`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=899, ge=60, le=86400)`
- **L896** `intconst` n -> n+1 — `1da12b77c962`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=61, le=86400)`
- **L896** `intconst` n -> n-1 — `1425dfd1eb3d`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=59, le=86400)`
- **L896** `intconst` n -> n+1 — `80d33ce336f3`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86401)`
- **L896** `intconst` n -> n-1 — `b3704bb5eaa7`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86399)`
- **L901** `intconst` n -> n+1 — `1da3e0b00e95`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=4, ge=1, le=100)`
- **L901** `intconst` n -> n-1 — `d3445348463b`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=2, ge=1, le=100)`
- **L901** `intconst` n -> n+1 — `f7b4041d0914`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=2, le=100)`
- **L901** `intconst` n -> n-1 — `3d967d8e0cbc`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=0, le=100)`
- **L901** `intconst` n -> n+1 — `29f9a0493a2f`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=101)`
- **L901** `intconst` n -> n-1 — `37069676aefa`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=99)`
- **L1043** `loopjump` continue -> break — `3fd6e177c30b`
  - `continue`
  - `break`
- **L1115** `loopjump` continue -> break — `906c5d1fd179`
  - `continue`
  - `break`
- **L1158** `loopjump` continue -> break — `892b68ac9fc7`
  - `continue`
  - `break`
- **L1328** `boolconst` False -> True — `a0b841c82d52`
  - `is_loopback = False`
  - `is_loopback = True`
- **L1356** `intconst` n -> n+1 — `60c896c64c49`
  - `candidate = host[2:] if host.startswith('*.') else host`
  - `candidate = host[3:] if host.startswith('*.') else host`
- **L1356** `intconst` n -> n-1 — `bc9d765b9fcc`
  - `candidate = host[2:] if host.startswith('*.') else host`
  - `candidate = host[1:] if host.startswith('*.') else host`
- **L1357** `boolconst` False -> True — `b0ef856984c0`
  - `is_loopback = False`
  - `is_loopback = True`
- **L1396** `loopjump` continue -> break — `d99e97fe27e4`
  - `continue`
  - `break`
- **L1438** `intconst` n -> n+1 — `be5b4b591663`
  - `db_pool_size: int = Field(default=2, ge=1)`
  - `db_pool_size: int = Field(default=3, ge=1)`
- **L1438** `intconst` n -> n-1 — `de031b33f2ff`
  - `db_pool_size: int = Field(default=2, ge=1)`
  - `db_pool_size: int = Field(default=1, ge=1)`
- **L1438** `intconst` n -> n+1 — `983977da8afa`
  - `db_pool_size: int = Field(default=2, ge=1)`
  - `db_pool_size: int = Field(default=2, ge=2)`
- **L1438** `intconst` n -> n-1 — `bb596e2f8b15`
  - `db_pool_size: int = Field(default=2, ge=1)`
  - `db_pool_size: int = Field(default=2, ge=0)`
- **L1439** `intconst` n -> n+1 — `a326dd5bb14b`
  - `db_max_overflow: int = Field(default=2, ge=0)`
  - `db_max_overflow: int = Field(default=3, ge=0)`
- **L1439** `intconst` n -> n-1 — `7db744758ab1`
  - `db_max_overflow: int = Field(default=2, ge=0)`
  - `db_max_overflow: int = Field(default=1, ge=0)`
- **L1439** `intconst` n -> n+1 — `4f52b2fa9e57`
  - `db_max_overflow: int = Field(default=2, ge=0)`
  - `db_max_overflow: int = Field(default=2, ge=1)`
- **L1440** `intconst` n -> n+1 — `468ba0a5c3e7`
  - `db_pool_timeout: int = Field(default=30, ge=1)`
  - `db_pool_timeout: int = Field(default=31, ge=1)`
- **L1440** `intconst` n -> n-1 — `849454f623d0`
  - `db_pool_timeout: int = Field(default=30, ge=1)`
  - `db_pool_timeout: int = Field(default=29, ge=1)`
- **L1440** `intconst` n -> n+1 — `d4b76dd8de68`
  - `db_pool_timeout: int = Field(default=30, ge=1)`
  - `db_pool_timeout: int = Field(default=30, ge=2)`
- **L1440** `intconst` n -> n-1 — `259699d27af3`
  - `db_pool_timeout: int = Field(default=30, ge=1)`
  - `db_pool_timeout: int = Field(default=30, ge=0)`
- **L1441** `intconst` n -> n+1 — `6b77a405dfa1`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=1)`
  - `db_statement_timeout_ms: int = Field(default=30001, ge=1)`
- **L1441** `intconst` n -> n-1 — `a2559d23cafc`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=1)`
  - `db_statement_timeout_ms: int = Field(default=29999, ge=1)`
- **L1441** `intconst` n -> n+1 — `f3ad852d47c4`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=1)`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=2)`
- **L1441** `intconst` n -> n-1 — `7e7c02a8882f`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=1)`
  - `db_statement_timeout_ms: int = Field(default=30000, ge=0)`
- **L1449** `intconst` n -> n+1 — `054450ad343f`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=100)`
  - `screening_s3_prefix: str = Field(default='raw', min_length=2, max_length=100)`
- **L1449** `intconst` n -> n-1 — `471f32b797b5`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=100)`
  - `screening_s3_prefix: str = Field(default='raw', min_length=0, max_length=100)`
- **L1449** `intconst` n -> n+1 — `f6c81138ece4`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=100)`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=101)`
- **L1449** `intconst` n -> n-1 — `5cb7bbf7f8f8`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=100)`
  - `screening_s3_prefix: str = Field(default='raw', min_length=1, max_length=99)`
- **L1450** `intconst` n -> n+1 — `df714bf2a61e`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=30)`
  - `screening_poll_interval_seconds: int = Field(default=301, ge=30)`
- **L1450** `intconst` n -> n-1 — `7383b7a13803`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=30)`
  - `screening_poll_interval_seconds: int = Field(default=299, ge=30)`
- **L1450** `intconst` n -> n+1 — `c0a88a9368dd`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=30)`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=31)`
- **L1450** `intconst` n -> n-1 — `5864a414db1a`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=30)`
  - `screening_poll_interval_seconds: int = Field(default=300, ge=29)`
- **L1451** `intconst` n -> n+1 — `9152b603fb06`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=51, ge=1, le=1000)`
- **L1451** `intconst` n -> n-1 — `ea40bbf6f323`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=49, ge=1, le=1000)`
- **L1451** `intconst` n -> n+1 — `f09dc1c68863`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=2, le=1000)`
- **L1451** `intconst` n -> n-1 — `7ff2254b6e9e`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=0, le=1000)`
- **L1451** `intconst` n -> n+1 — `fe5201fe183a`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1001)`
- **L1451** `intconst` n -> n-1 — `72e8262fbf9d`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=1000)`
  - `screening_max_images_per_cycle: int = Field(default=50, ge=1, le=999)`
- **L1459** `intconst` n -> n+1 — `aad3bf69e420`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=401, ge=1, le=100000)`
- **L1459** `intconst` n -> n-1 — `6f853e4bcb15`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=399, ge=1, le=100000)`
- **L1459** `intconst` n -> n+1 — `7b24069b9332`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=2, le=100000)`
- **L1459** `intconst` n -> n-1 — `8810e93387ef`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=0, le=100000)`
- **L1459** `intconst` n -> n+1 — `f1adf87329b3`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100001)`
- **L1459** `intconst` n -> n-1 — `936613ecd2c3`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=100000)`
  - `screening_daily_call_budget_per_farm: int = Field(default=400, ge=1, le=99999)`
- **L1460** `intconst` n -> n+1 — `35f5f035cf23`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1569, ge=256, le=4096)`
- **L1460** `intconst` n -> n-1 — `30d6f4547584`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1567, ge=256, le=4096)`
- **L1460** `intconst` n -> n+1 — `7fa8b5c269b7`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=257, le=4096)`
- **L1460** `intconst` n -> n-1 — `890b6f42f98e`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=255, le=4096)`
- **L1460** `intconst` n -> n+1 — `97681f47c2c0`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4097)`
- **L1460** `intconst` n -> n-1 — `f0a01df1a247`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4096)`
  - `screening_image_max_edge_px: int = Field(default=1568, ge=256, le=4095)`
- **L1461** `boolconst` True -> False — `eff0b402131a`
  - `screening_crop_detection_enabled: bool = True`
  - `screening_crop_detection_enabled: bool = False`
- **L1462** `intconst` n -> n+1 — `4b24d1c987b1`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=9, ge=1, le=20)`
- **L1462** `intconst` n -> n-1 — `3e49954ad3a8`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=7, ge=1, le=20)`
- **L1462** `intconst` n -> n+1 — `4bf44b218aa2`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=8, ge=2, le=20)`
- **L1462** `intconst` n -> n-1 — `d5231f2d6173`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=8, ge=0, le=20)`
- **L1462** `intconst` n -> n+1 — `2ad85a612fb6`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=21)`
- **L1462** `intconst` n -> n-1 — `3ffa9b29e82b`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=20)`
  - `screening_max_crops_per_image: int = Field(default=8, ge=1, le=19)`
- **L1463** `intconst` n -> n+1 — `752ae6a81069`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=901, ge=60, le=86400)`
- **L1463** `intconst` n -> n-1 — `eeec90c24e64`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=899, ge=60, le=86400)`
- **L1463** `intconst` n -> n+1 — `0f11a5cbedff`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=61, le=86400)`
- **L1463** `intconst` n -> n-1 — `b188e68c5d26`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=59, le=86400)`
- **L1463** `intconst` n -> n+1 — `aba3264ecfbd`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86401)`
- **L1463** `intconst` n -> n-1 — `ffaa971907ea`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_presign_expiry_seconds: int = Field(default=900, ge=60, le=86399)`
- **L1472** `intconst` n -> n+1 — `b2e5e8fc00e2`
  - `screening_provider_rotation: list[ScreeningRotationProvider] = Field(default_factory=list, max_length=8)`
  - `screening_provider_rotation: list[ScreeningRotationProvider] = Field(default_factory=list, max_length=9)`
- **L1472** `intconst` n -> n-1 — `62e5c67953f7`
  - `screening_provider_rotation: list[ScreeningRotationProvider] = Field(default_factory=list, max_length=8)`
  - `screening_provider_rotation: list[ScreeningRotationProvider] = Field(default_factory=list, max_length=7)`
- **L1474** `intconst` n -> n+1 — `f56dddf6000b`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=121, ge=10, le=600)`
- **L1474** `intconst` n -> n-1 — `43e0087fbd24`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=119, ge=10, le=600)`
- **L1474** `intconst` n -> n+1 — `1779b4ecffb7`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=11, le=600)`
- **L1474** `intconst` n -> n-1 — `af05dbd12ff9`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=9, le=600)`
- **L1474** `intconst` n -> n+1 — `77c69c4e78b3`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=601)`
- **L1474** `intconst` n -> n-1 — `2de715ee4c05`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=600)`
  - `screening_provider_timeout_seconds: int = Field(default=120, ge=10, le=599)`
- **L1480** `intconst` n -> n+1 — `4520fc502055`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1801, ge=600, le=86400)`
- **L1480** `intconst` n -> n-1 — `369c65157d00`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1799, ge=600, le=86400)`
- **L1480** `intconst` n -> n+1 — `03e515cca410`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=601, le=86400)`
- **L1480** `intconst` n -> n-1 — `860a3ecf29cb`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=599, le=86400)`
- **L1480** `intconst` n -> n+1 — `067c61e1292a`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86401)`
- **L1480** `intconst` n -> n-1 — `4d09a03856ce`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86400)`
  - `screening_stale_processing_after_seconds: int = Field(default=1800, ge=600, le=86399)`
- **L1487** `intconst` n -> n+1 — `1e40390aa14e`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=901, ge=60, le=86400)`
- **L1487** `intconst` n -> n-1 — `706b7e1de094`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=899, ge=60, le=86400)`
- **L1487** `intconst` n -> n+1 — `610b7d349906`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=61, le=86400)`
- **L1487** `intconst` n -> n-1 — `7766edddc9a1`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=59, le=86400)`
- **L1487** `intconst` n -> n+1 — `93638e7469cf`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86401)`
- **L1487** `intconst` n -> n-1 — `860e44983709`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86400)`
  - `screening_worker_health_max_age_seconds: int = Field(default=900, ge=60, le=86399)`
- **L1491** `intconst` n -> n+1 — `9421dd546ea2`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=4, ge=1, le=100)`
- **L1491** `intconst` n -> n-1 — `b32587850b21`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=2, ge=1, le=100)`
- **L1491** `intconst` n -> n+1 — `39ca8a2a35d4`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=2, le=100)`
- **L1491** `intconst` n -> n-1 — `583c091db9cb`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=0, le=100)`
- **L1491** `intconst` n -> n+1 — `97776fc2fadd`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=101)`
- **L1491** `intconst` n -> n-1 — `215cdf839edd`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=100)`
  - `screening_worker_max_consecutive_cycle_failures: int = Field(default=3, ge=1, le=99)`

### `app/deps.py` (2 survivors)

- **L95** `ifexp` swap branches — `94826ab61e7b`
  - `ip_key = request.client.host if request.client else 'unknown'`
  - `ip_key = 'unknown' if request.client else request.client.host`
- **L165** `boolconst` False -> True — `794f3854e9c1`
  - `security_event('auth.token.invalid', 'access token failed verification (not merely expired)', expired=False)`
  - `security_event('auth.token.invalid', 'access token failed verification (not merely expired)', expired=True)`

### `app/main.py` (9 survivors)

- **L112** `boolop` Or -> And — `cc4b4b5a6423`
  - `raw_path = scope.get('raw_path') or str(scope.get('path', '')).encode('utf-8')`
  - `raw_path = scope.get('raw_path') and str(scope.get('path', '')).encode('utf-8')`
- **L928** `ifexp` swap branches — `a9f17e9d6ffd`
  - `request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex`
  - `request_id = uuid.uuid4().hex if _REQUEST_ID_RE.fullmatch(incoming) else incoming`
- **L935** `intconst` n -> n+1 — `143baa3a1d15`
  - `status_code = 500`
  - `status_code = 501`
- **L935** `intconst` n -> n-1 — `058a1ca45340`
  - `status_code = 500`
  - `status_code = 499`
- **L948** `binop` Mult -> Div — `276679074200`
  - `duration_ms = (time.perf_counter() - started) * 1000`
  - `duration_ms = (time.perf_counter() - started) / 1000`
- **L948** `binop` Sub -> Add — `92725fa0592f`
  - `duration_ms = (time.perf_counter() - started) * 1000`
  - `duration_ms = (time.perf_counter() + started) * 1000`
- **L948** `intconst` n -> n+1 — `783ca264b036`
  - `duration_ms = (time.perf_counter() - started) * 1000`
  - `duration_ms = (time.perf_counter() - started) * 1001`
- **L953** `intconst` n -> n+1 — `772478a3dca7`
  - `metrics.observe_http_request(_metrics_method(request), _route_template(request), status_code, duration_ms / 1000)`
  - `metrics.observe_http_request(_metrics_method(request), _route_template(request), status_code, duration_ms / 1001)`
- **L953** `intconst` n -> n-1 — `251fbcf04147`
  - `metrics.observe_http_request(_metrics_method(request), _route_template(request), status_code, duration_ms / 1000)`
  - `metrics.observe_http_request(_metrics_method(request), _route_template(request), status_code, duration_ms / 999)`

### `app/ratelimit.py` (7 survivors)

- **L149** `boolconst` True -> False — `361c9338ccbe`
  - `self._reclassify(bucket, touch=True)`
  - `self._reclassify(bucket, touch=False)`
- **L155** `boolconst` True -> False — `3fb6892e5299`
  - `self._reclassify(bucket, touch=True)`
  - `self._reclassify(bucket, touch=False)`
- **L188** `boolconst` True -> False — `5d94dc6e91da`
  - `self._reclassify(bucket, touch=True)`
  - `self._reclassify(bucket, touch=False)`
- **L227** `binop` Add -> Sub — `309b4066b4bf`
  - `self._next_sweep = self._clock() + self._sweep_interval_seconds`
  - `self._next_sweep = self._clock() - self._sweep_interval_seconds`
- **L283** `binop` Add -> Sub — `0101f19e5a5d`
  - `self._next_sweep = now + self._sweep_interval_seconds`
  - `self._next_sweep = now - self._sweep_interval_seconds`
- **L298** `boolconst` True -> False — `336ac58ab6b0`
  - `self._reclassify(bucket, touch=True)`
  - `self._reclassify(bucket, touch=False)`
- **L483** `intconst` n -> n+1 — `835ed9e0e023`
  - `_throttle_rejections[scope] += 1`
  - `_throttle_rejections[scope] += 2`

### `app/schemas/health.py` (5 survivors)

- **L49** `intconst` n -> n+1 — `47f79856bf69`
  - `clearance_reference: PostgresText = Field(min_length=1, max_length=255)`
  - `clearance_reference: PostgresText = Field(min_length=2, max_length=255)`
- **L256** `intconst` n -> n-1 — `801df336a742`
  - `vet_name: PostgresText | None = Field(default=None, max_length=120)`
  - `vet_name: PostgresText | None = Field(default=None, max_length=119)`
- **L259** `intconst` n -> n+1 — `baf43c8d167b`
  - `schedule_template_name: PostgresText | None = Field(default=None, max_length=120)`
  - `schedule_template_name: PostgresText | None = Field(default=None, max_length=121)`
- **L269** `intconst` n -> n+1 — `e16410fb7bc1`
  - `administered_by: PostgresText | None = Field(default=None, max_length=120)`
  - `administered_by: PostgresText | None = Field(default=None, max_length=121)`
- **L269** `intconst` n -> n-1 — `99efa7f33b9a`
  - `administered_by: PostgresText | None = Field(default=None, max_length=120)`
  - `administered_by: PostgresText | None = Field(default=None, max_length=119)`

### `app/schemas/screening.py` (7 survivors)

- **L41** `intconst` n -> n-1 — `106ce8281a40`
  - `MAX_SCREENING_UPLOAD_BYTES = 25 * 1024 * 1024`
  - `MAX_SCREENING_UPLOAD_BYTES = 24 * 1024 * 1024`
- **L103** `boolconst` True -> False — `27407ab52e83`
  - `model_config = ConfigDict(from_attributes=True)`
  - `model_config = ConfigDict(from_attributes=False)`
- **L254** `boolconst` True -> False — `8d7fb24302e2`
  - `model_config = ConfigDict(from_attributes=True)`
  - `model_config = ConfigDict(from_attributes=False)`
- **L261** `intconst` n -> n+1 — `440d73d68b33`
  - `images_flagged: int = 0`
  - `images_flagged: int = 1`
- **L283** `boolconst` True -> False — `97e744b75e8a`
  - `model_config = ConfigDict(str_strip_whitespace=True)`
  - `model_config = ConfigDict(str_strip_whitespace=False)`
- **L288** `intconst` n -> n+1 — `8d5e7558c5bc`
  - `file_name: str = Field(min_length=5, max_length=255, pattern='^[\\w.\\- ]+\\.(?i:jpe?g|png)$')`
  - `file_name: str = Field(min_length=5, max_length=256, pattern='^[\\w.\\- ]+\\.(?i:jpe?g|png)$')`
- **L294** `intconst` n -> n-1 — `7b7b45f48910`
  - `file_size: int = Field(ge=1, le=MAX_SCREENING_UPLOAD_BYTES)`
  - `file_size: int = Field(ge=0, le=MAX_SCREENING_UPLOAD_BYTES)`

### `app/security.py` (33 survivors)

- **L228** `intconst` n -> n+1 — `6cddb087c248`
  - `remaining = max(get_settings().rejected_login_pbkdf2_work_budget - used_iterations, 0)`
  - `remaining = max(get_settings().rejected_login_pbkdf2_work_budget - used_iterations, 1)`
- **L363** `binop` BitOr -> BitAnd — `734d69dbd08a`
  - `fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)`
  - `fd = os.open(path, (os.O_RDONLY | os.O_CLOEXEC) & os.O_NONBLOCK)`
- **L363** `binop` BitOr -> BitAnd — `5b155eaac29a`
  - `fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)`
  - `fd = os.open(path, os.O_RDONLY & os.O_CLOEXEC | os.O_NONBLOCK)`
- **L372** `binop` Sub -> Add — `0e08bd2d0cd8`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 + len(raw)))`
- **L372** `intconst` n -> n+1 — `38451df940d7`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(65 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
- **L372** `intconst` n -> n-1 — `544992ece19f`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(63 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
- **L372** `intconst` n -> n+1 — `a0a4c9e644ce`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1025, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
- **L372** `intconst` n -> n-1 — `a9a9a112e444`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1023, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
- **L372** `binop` Add -> Sub — `2193e9062dc9`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES - 1 - len(raw)))`
- **L372** `intconst` n -> n-1 — `5941e927d258`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 0 - len(raw)))`
- **L433** `intconst` n -> n+1 — `04d5b99fb14d`
  - `key = rsa.generate_private_key(public_exponent=65537, key_size=2048)`
  - `key = rsa.generate_private_key(public_exponent=65537, key_size=2049)`
- **L448** `intconst` n -> n+1 — `b10e04e64092`
  - `_write_atomic(pub, key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo), mode=420)`
  - `_write_atomic(pub, key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo), mode=421)`
- **L448** `intconst` n -> n-1 — `e84a7e2929df`
  - `_write_atomic(pub, key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo), mode=420)`
  - `_write_atomic(pub, key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo), mode=419)`
- **L499** `boolconst` True -> False — `d8102999df1e`
  - `key_dir.mkdir(parents=True, mode=448, exist_ok=True)`
  - `key_dir.mkdir(parents=False, mode=448, exist_ok=True)`
- **L505** `binop` BitAnd -> BitOr — `c9b8dab84890`
  - `existing_mode = key_dir.stat().st_mode & 4095`
  - `existing_mode = key_dir.stat().st_mode | 4095`
- **L505** `intconst` n -> n+1 — `8a3a7a9013a2`
  - `existing_mode = key_dir.stat().st_mode & 4095`
  - `existing_mode = key_dir.stat().st_mode & 4096`
- **L505** `intconst` n -> n-1 — `cb68888113bf`
  - `existing_mode = key_dir.stat().st_mode & 4095`
  - `existing_mode = key_dir.stat().st_mode & 4094`
- **L506** `binop` BitOr -> BitAnd — `f758872a83ea`
  - `private_mode = (existing_mode | 448) & ~63`
  - `private_mode = existing_mode & 448 & ~63`
- **L506** `intconst` n -> n+1 — `1d28134c8738`
  - `private_mode = (existing_mode | 448) & ~63`
  - `private_mode = (existing_mode | 449) & ~63`
- **L506** `intconst` n -> n-1 — `321cdf32fe20`
  - `private_mode = (existing_mode | 448) & ~63`
  - `private_mode = (existing_mode | 447) & ~63`
- **L528** `intconst` n -> n+1 — `488ec4ffed06`
  - `lock_fd = os.open(lock_path, lock_flags, 384)`
  - `lock_fd = os.open(lock_path, lock_flags, 385)`
- **L528** `intconst` n -> n-1 — `695104700418`
  - `lock_fd = os.open(lock_path, lock_flags, 384)`
  - `lock_fd = os.open(lock_path, lock_flags, 383)`
- **L543** `boolop` And -> Or — `b50aeefca777`
  - `pair_complete = priv.exists() and pub.exists()`
  - `pair_complete = priv.exists() or pub.exists()`
- **L955** `intconst` n -> n+1 — `013fb617c9c7`
  - `counter = step.to_bytes(8, 'big')`
  - `counter = step.to_bytes(9, 'big')`
- **L955** `intconst` n -> n-1 — `f331a24fcc94`
  - `counter = step.to_bytes(8, 'big')`
  - `counter = step.to_bytes(7, 'big')`
- **L957** `intconst` n -> n+1 — `481d74001465`
  - `offset = digest[-1] & 15`
  - `offset = digest[-1] & 16`
- **L957** `intconst` n -> n-1 — `798c67a32bf7`
  - `offset = digest[-1] & 15`
  - `offset = digest[-1] & 14`
- **L957** `intconst` n -> n+1 — `ba30e375c79b`
  - `offset = digest[-1] & 15`
  - `offset = digest[-2] & 15`
- **L957** `intconst` n -> n-1 — `e8bc4d6dd1aa`
  - `offset = digest[-1] & 15`
  - `offset = digest[-0] & 15`
- **L958** `intconst` n -> n-1 — `b6412a8d3878`
  - `binary = int.from_bytes(digest[offset:offset + 4], 'big') & 2147483647`
  - `binary = int.from_bytes(digest[offset:offset + 4], 'big') & 2147483646`
- **L958** `intconst` n -> n+1 — `e4c40b10eb71`
  - `binary = int.from_bytes(digest[offset:offset + 4], 'big') & 2147483647`
  - `binary = int.from_bytes(digest[offset:offset + 5], 'big') & 2147483647`
- **L958** `intconst` n -> n-1 — `c55261de6ad1`
  - `binary = int.from_bytes(digest[offset:offset + 4], 'big') & 2147483647`
  - `binary = int.from_bytes(digest[offset:offset + 3], 'big') & 2147483647`
- **L990** `intconst` n -> n+1 — `5a14279dd7b2`
  - `floor_step = 0 if last_used_step is None else last_used_step + 1`
  - `floor_step = 1 if last_used_step is None else last_used_step + 1`

### `app/seed.py` (13 survivors)

- **L167** `intconst` n -> n+1 — `c89d0e62e7f6`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 4, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
- **L167** `intconst` n -> n-1 — `0643a32787f7`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 2, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
- **L179** `intconst` n -> n+1 — `2421d14e294e`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 43, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
- **L179** `intconst` n -> n-1 — `8a17dd2c7dc5`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 41, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
- **L180** `intconst` n -> n+1 — `88610bd85b35`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 29, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
- **L180** `intconst` n -> n-1 — `b463729b4a08`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 27, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
- **L194** `intconst` n -> n+1 — `e71e58676326`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 61, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
- **L194** `intconst` n -> n-1 — `4003980788ab`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 59, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
- **L196** `intconst` n -> n+1 — `59e512036171`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 10, CONC), ('Mineral mix', 1, CONC)])]`
- **L196** `intconst` n -> n-1 — `f7b0bd6f5a44`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 8, CONC), ('Mineral mix', 1, CONC)])]`
- **L197** `intconst` n -> n+1 — `468f5b13f9c4`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 1, CONC)])]`
  - `FEED_RECIPES: list[tuple[str, str, str, list[tuple[str, float, str]]]] = [('FATTENING_50_50', 'Fattening 50:50', 'Male kids day 91 → sale (50% roughage / 50% concentrate)', [(GREEN, 30, WET), (DRY_STOVER, 20, DRY), ('Crushed maize', 17.5, CONC), ('Maize DDGS', 10, CONC), ('Soya DOC', 7.5, CONC), ('Mustard DOC', 7.5, CONC), ('DORB', 6, CONC), ('Mineral mix', 1.5, CONC)]), ('LACTATING_60_40', 'Lactating 60:40', 'Lactating does, growing doelings, frame-builder kids day 61–90', [(GREEN, 36, WET), (DRY_STOVER, 24, DRY), ('Crushed maize', 14, CONC), ('Maize DDGS', 4.8, CONC), ('Soya DOC', 6, CONC), ('Mustard DOC', 10, CONC), ('DORB', 4, CONC), ('Mineral mix', 1.2, CONC)]), ('MAINTENANCE_75_25', 'Maintenance 75:25', 'Resting dry-off, breeding, early pregnancy, dry bucks', [(GREEN, 45, WET), (DRY_STOVER, 30, DRY), ('Crushed maize', 7.5, CONC), ('Maize DDGS', 2.5, CONC), ('Soya DOC', 3, CONC), ('Mustard DOC', 3.75, CONC), ('DORB', 5.25, CONC), ('Mineral mix', 2.0, CONC), ('Salt', 1.0, CONC)]), ('FLUSH_70_30', 'Flush 70:30', '3–4 weeks pre-breeding flush; resting does days ~10–30', [(GREEN, 42, WET), (DRY_STOVER, 28, DRY), ('Crushed maize', 10.5, CONC), ('Maize DDGS', 4.5, CONC), ('Soya DOC', 4.5, CONC), ('Mustard DOC', 4.5, CONC), ('DORB', 5.1, CONC), ('Mineral mix', 0.9, CONC)]), ('CREEP', 'Creep feed', 'Kids weeks 2–8, dry concentrate only (approx 60/30/10 + mineral)', [('Crushed maize', 60, CONC), ('Soya DOC', 30, CONC), ('Maize DDGS', 9, CONC), ('Mineral mix', 2, CONC)])]`
- **L639** `compare` Eq -> NotEq — `0dfa9d0a8e1c`
  - `membership_role_exists = select(FarmMembership.id).join(Role, Role.id == FarmMembership.role_id).where(FarmMembership.farm_id == Task.farm_id, FarmMembership.user_id == Task.assigned_user_id, Role.deleted_at.is_(None)).correlate(Task).exists()`
  - `membership_role_exists = select(FarmMembership.id).join(Role, Role.id == FarmMembership.role_id).where(FarmMembership.farm_id != Task.farm_id, FarmMembership.user_id == Task.assigned_user_id, Role.deleted_at.is_(None)).correlate(Task).exists()`
- **L640** `compare` Eq -> NotEq — `c1896a51eac4`
  - `membership_role_exists = select(FarmMembership.id).join(Role, Role.id == FarmMembership.role_id).where(FarmMembership.farm_id == Task.farm_id, FarmMembership.user_id == Task.assigned_user_id, Role.deleted_at.is_(None)).correlate(Task).exists()`
  - `membership_role_exists = select(FarmMembership.id).join(Role, Role.id == FarmMembership.role_id).where(FarmMembership.farm_id == Task.farm_id, FarmMembership.user_id != Task.assigned_user_id, Role.deleted_at.is_(None)).correlate(Task).exists()`

### `app/services/animals.py` (1 survivors)

- **L154** `compare` GtE -> Gt — `6ab03a5f21d7`
  - `ready = bool(animal.status == AnimalStatus.ACTIVE.value and (not animal.movement_restricted) and (not animal.suspected_scheduled_disease) and (age is not None) and (age >= profile.min_sire_breeding_age_months) and (latest_weight_kg is not None) and (latest_weight_kg >= profile.min_sire_breeding_weight_kg))`
  - `ready = bool(animal.status == AnimalStatus.ACTIVE.value and (not animal.movement_restricted) and (not animal.suspected_scheduled_disease) and (age is not None) and (age > profile.min_sire_breeding_age_months) and (latest_weight_kg is not None) and (latest_weight_kg >= profile.min_sire_breeding_weight_kg))`

### `app/services/breeding.py` (2 survivors)

- **L196** `boolop` Or -> And — `233bb266acda`
  - `when = reference_date or today(farm.timezone)`
  - `when = reference_date and today(farm.timezone)`
- **L555** `binop` Add -> Sub — `5aeb41e8e5fc`
  - `vwp_floor = latest_calving + timedelta(days=profile.voluntary_waiting_days)`
  - `vwp_floor = latest_calving - timedelta(days=profile.voluntary_waiting_days)`

### `app/services/cadence.py` (17 survivors)

- **L42** `intconst` n -> n+1 — `fd3a1decbcf6`
  - `_MAX_REORDER_INGREDIENTS = 100`
  - `_MAX_REORDER_INGREDIENTS = 101`
- **L42** `intconst` n -> n-1 — `017c28a15915`
  - `_MAX_REORDER_INGREDIENTS = 100`
  - `_MAX_REORDER_INGREDIENTS = 99`
- **L52** `intconst` n -> n+1 — `d0c82af47ad4`
  - `_INTERVAL_FORWARD_DEDUPE_DAYS = 30`
  - `_INTERVAL_FORWARD_DEDUPE_DAYS = 31`
- **L52** `intconst` n -> n-1 — `ad5911f23d98`
  - `_INTERVAL_FORWARD_DEDUPE_DAYS = 30`
  - `_INTERVAL_FORWARD_DEDUPE_DAYS = 29`
- **L119** `intconst` n -> n-1 — `dc1bb21f2557`
  - `_INTERVAL_ROUNDS: tuple[_IntervalRound, ...] = ((TaskCategory.HOOF_TRIMMING, 182, 'hoof_trimming_round', 'Hoof trimming round (6-monthly) — trim all ages, heel to toe'), (TaskCategory.SPRAYING, 182, 'ectoparasite_spray_round', 'Ectoparasite spray/dip round (Butox/deltamethrin) — never heavily pregnant does'), (TaskCategory.DISINFECTION, 91, 'shed_disinfection_round', 'Shed disinfection round — disinfect + lime; extra attention to kidding pens'), (TaskCategory.WEIGHING, 30, 'monthly_weighing_round', 'Monthly weighing round — record weights; grow-out buckets first'))`
  - `_INTERVAL_ROUNDS: tuple[_IntervalRound, ...] = ((TaskCategory.HOOF_TRIMMING, 181, 'hoof_trimming_round', 'Hoof trimming round (6-monthly) — trim all ages, heel to toe'), (TaskCategory.SPRAYING, 182, 'ectoparasite_spray_round', 'Ectoparasite spray/dip round (Butox/deltamethrin) — never heavily pregnant does'), (TaskCategory.DISINFECTION, 91, 'shed_disinfection_round', 'Shed disinfection round — disinfect + lime; extra attention to kidding pens'), (TaskCategory.WEIGHING, 30, 'monthly_weighing_round', 'Monthly weighing round — record weights; grow-out buckets first'))`
- **L137** `intconst` n -> n+1 — `58795341c8a6`
  - `_INTERVAL_ROUNDS: tuple[_IntervalRound, ...] = ((TaskCategory.HOOF_TRIMMING, 182, 'hoof_trimming_round', 'Hoof trimming round (6-monthly) — trim all ages, heel to toe'), (TaskCategory.SPRAYING, 182, 'ectoparasite_spray_round', 'Ectoparasite spray/dip round (Butox/deltamethrin) — never heavily pregnant does'), (TaskCategory.DISINFECTION, 91, 'shed_disinfection_round', 'Shed disinfection round — disinfect + lime; extra attention to kidding pens'), (TaskCategory.WEIGHING, 30, 'monthly_weighing_round', 'Monthly weighing round — record weights; grow-out buckets first'))`
  - `_INTERVAL_ROUNDS: tuple[_IntervalRound, ...] = ((TaskCategory.HOOF_TRIMMING, 182, 'hoof_trimming_round', 'Hoof trimming round (6-monthly) — trim all ages, heel to toe'), (TaskCategory.SPRAYING, 182, 'ectoparasite_spray_round', 'Ectoparasite spray/dip round (Butox/deltamethrin) — never heavily pregnant does'), (TaskCategory.DISINFECTION, 91, 'shed_disinfection_round', 'Shed disinfection round — disinfect + lime; extra attention to kidding pens'), (TaskCategory.WEIGHING, 31, 'monthly_weighing_round', 'Monthly weighing round — record weights; grow-out buckets first'))`
- **L245** `boolconst` False -> True — `95dd099fe621`
  - `created = False`
  - `created = True`
- **L267** `loopjump` continue -> break — `b1c6148a7a41`
  - `continue`
  - `break`
- **L278** `boolconst` True -> False — `54977fb1db68`
  - `created = True`
  - `created = False`
- **L292** `boolconst` False -> True — `b488f470ee07`
  - `created = False`
  - `created = True`
- **L399** `boolconst` False -> True — `33ed2cf1370d`
  - `created = False`
  - `created = True`
- **L435** `boolconst` True -> False — `31e96cf2593a`
  - `created = True`
  - `created = False`
- **L484** `boolconst` False -> True — `54b9fc412ec5`
  - `created = False`
  - `created = True`
- **L488** `loopjump` continue -> break — `8b40af2ac71e`
  - `continue`
  - `break`
- **L508** `boolconst` True -> False — `3b756d4864cc`
  - `created = True`
  - `created = False`
- **L547** `boolconst` False -> True — `678e73668bf3`
  - `created = False`
  - `created = True`
- **L560** `intconst` n -> n+1 — `afe1428597d3`
  - `async def ensure_cadence_farm_batch(db: AsyncSession, *, batch_size: int, after_farm_id: int=0) -> tuple[int, int]:
    """Materialize one bounded keyset page of farms through the cadence sweep.

    Returns ``(farms_processed, last_farm_id)`` so the caller can page the
    whole tenant list with ``after_farm_id`` and stop on a short page. Farm
    rows are deliberately NOT locked here (a Farm row lock would add the
    inverse Farm -> Animal lock-order edge); the per-farm advisory lock
    inside ``ensure_cadence_tasks`` serializes any overlap between sweeps or
    with a concurrent recurrence run.
    """
    if not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be between 1 and 1000')
    farms = list((await db.execute(select(Farm).where(Farm.id > after_farm_id).order_by(Farm.id).limit(batch_size))).scalars().all())
    farm_ids = [farm.id for farm in farms]
    for farm, farm_id in zip(farms, farm_ids, strict=True):
        try:
            await ensure_cadence_tasks(db, farm)
        except Exception:
            await db.rollback()
            _logger.exception('cadence materialization failed for farm_id=%s; skipping to the next farm (cursor still advances)', farm_id)
    return (len(farm_ids), farm_ids[-1] if farm_ids else after_farm_id)`
  - `async def ensure_cadence_farm_batch(db: AsyncSession, *, batch_size: int, after_farm_id: int=1) -> tuple[int, int]:
    """Materialize one bounded keyset page of farms through the cadence sweep.

    Returns ``(farms_processed, last_farm_id)`` so the caller can page the
    whole tenant list with ``after_farm_id`` and stop on a short page. Farm
    rows are deliberately NOT locked here (a Farm row lock would add the
    inverse Farm -> Animal lock-order edge); the per-farm advisory lock
    inside ``ensure_cadence_tasks`` serializes any overlap between sweeps or
    with a concurrent recurrence run.
    """
    if not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be between 1 and 1000')
    farms = list((await db.execute(select(Farm).where(Farm.id > after_farm_id).order_by(Farm.id).limit(batch_size))).scalars().all())
    farm_ids = [farm.id for farm in farms]
    for farm, farm_id in zip(farms, farm_ids, strict=True):
        try:
            await ensure_cadence_tasks(db, farm)
        except Exception:
            await db.rollback()
            _logger.exception('cadence materialization failed for farm_id=%s; skipping to the next farm (cursor still advances)', farm_id)
    return (len(farm_ids), farm_ids[-1] if farm_ids else after_farm_id)`

### `app/services/chronology.py` (7 survivors)

- **L146** `compare` Eq -> NotEq — `51f534320e08`
  - `breeding_facts = select(func.min(BreedingRecord.breeding_date).label('first_breeding_date'), func.min(BreedingRecord.ultrasound_result_date).filter(BreedingRecord.doe_id == animal.id).label('first_ultrasound_result_date'), func.min(BreedingRecord.loss_date).filter(BreedingRecord.doe_id == animal.id).label('first_loss_date')).where(BreedingRecord.farm_id == animal.farm_id, or_(BreedingRecord.doe_id == animal.id, BreedingRecord.buck_id == animal.id)).subquery()`
  - `breeding_facts = select(func.min(BreedingRecord.breeding_date).label('first_breeding_date'), func.min(BreedingRecord.ultrasound_result_date).filter(BreedingRecord.doe_id == animal.id).label('first_ultrasound_result_date'), func.min(BreedingRecord.loss_date).filter(BreedingRecord.doe_id == animal.id).label('first_loss_date')).where(BreedingRecord.farm_id != animal.farm_id, or_(BreedingRecord.doe_id == animal.id, BreedingRecord.buck_id == animal.id)).subquery()`
- **L147** `compare` Eq -> NotEq — `a196245fe889`
  - `breeding_facts = select(func.min(BreedingRecord.breeding_date).label('first_breeding_date'), func.min(BreedingRecord.ultrasound_result_date).filter(BreedingRecord.doe_id == animal.id).label('first_ultrasound_result_date'), func.min(BreedingRecord.loss_date).filter(BreedingRecord.doe_id == animal.id).label('first_loss_date')).where(BreedingRecord.farm_id == animal.farm_id, or_(BreedingRecord.doe_id == animal.id, BreedingRecord.buck_id == animal.id)).subquery()`
  - `breeding_facts = select(func.min(BreedingRecord.breeding_date).label('first_breeding_date'), func.min(BreedingRecord.ultrasound_result_date).filter(BreedingRecord.doe_id == animal.id).label('first_ultrasound_result_date'), func.min(BreedingRecord.loss_date).filter(BreedingRecord.doe_id == animal.id).label('first_loss_date')).where(BreedingRecord.farm_id == animal.farm_id, or_(BreedingRecord.doe_id != animal.id, BreedingRecord.buck_id == animal.id)).subquery()`
- **L147** `compare` Eq -> NotEq — `55f8f0864c42`
  - `breeding_facts = select(func.min(BreedingRecord.breeding_date).label('first_breeding_date'), func.min(BreedingRecord.ultrasound_result_date).filter(BreedingRecord.doe_id == animal.id).label('first_ultrasound_result_date'), func.min(BreedingRecord.loss_date).filter(BreedingRecord.doe_id == animal.id).label('first_loss_date')).where(BreedingRecord.farm_id == animal.farm_id, or_(BreedingRecord.doe_id == animal.id, BreedingRecord.buck_id == animal.id)).subquery()`
  - `breeding_facts = select(func.min(BreedingRecord.breeding_date).label('first_breeding_date'), func.min(BreedingRecord.ultrasound_result_date).filter(BreedingRecord.doe_id == animal.id).label('first_ultrasound_result_date'), func.min(BreedingRecord.loss_date).filter(BreedingRecord.doe_id == animal.id).label('first_loss_date')).where(BreedingRecord.farm_id == animal.farm_id, or_(BreedingRecord.doe_id == animal.id, BreedingRecord.buck_id != animal.id)).subquery()`
- **L162** `compare` Eq -> NotEq — `ecebd96f10a4`
  - `first_health = select(func.min(HealthEvent.date)).where(HealthEvent.farm_id == animal.farm_id, HealthEvent.animal_id == animal.id).scalar_subquery()`
  - `first_health = select(func.min(HealthEvent.date)).where(HealthEvent.farm_id != animal.farm_id, HealthEvent.animal_id == animal.id).scalar_subquery()`
- **L163** `compare` Eq -> NotEq — `954db8a72c5a`
  - `first_health = select(func.min(HealthEvent.date)).where(HealthEvent.farm_id == animal.farm_id, HealthEvent.animal_id == animal.id).scalar_subquery()`
  - `first_health = select(func.min(HealthEvent.date)).where(HealthEvent.farm_id == animal.farm_id, HealthEvent.animal_id != animal.id).scalar_subquery()`
- **L170** `compare` Eq -> NotEq — `260cdc6fdde8`
  - `first_kidding = select(func.min(KiddingRecord.date)).where(KiddingRecord.farm_id == animal.farm_id, KiddingRecord.doe_id == animal.id).scalar_subquery()`
  - `first_kidding = select(func.min(KiddingRecord.date)).where(KiddingRecord.farm_id != animal.farm_id, KiddingRecord.doe_id == animal.id).scalar_subquery()`
- **L171** `compare` Eq -> NotEq — `290e1d705e12`
  - `first_kidding = select(func.min(KiddingRecord.date)).where(KiddingRecord.farm_id == animal.farm_id, KiddingRecord.doe_id == animal.id).scalar_subquery()`
  - `first_kidding = select(func.min(KiddingRecord.date)).where(KiddingRecord.farm_id == animal.farm_id, KiddingRecord.doe_id != animal.id).scalar_subquery()`

### `app/services/dashboard.py` (20 survivors)

- **L92** `binop` Add -> Sub — `b5d1bb3b3773`
  - `months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)`
  - `months = (reference_date.year - dob.year) * 12 - (reference_date.month - dob.month)`
- **L92** `binop` Mult -> Div — `12d42aede9a8`
  - `months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)`
  - `months = (reference_date.year - dob.year) / 12 + (reference_date.month - dob.month)`
- **L92** `binop` Sub -> Add — `6746a2c5b494`
  - `months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)`
  - `months = (reference_date.year - dob.year) * 12 + (reference_date.month + dob.month)`
- **L92** `binop` Sub -> Add — `58d5ee22d100`
  - `months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)`
  - `months = (reference_date.year + dob.year) * 12 + (reference_date.month - dob.month)`
- **L92** `intconst` n -> n+1 — `51ac71056ec1`
  - `months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)`
  - `months = (reference_date.year - dob.year) * 13 + (reference_date.month - dob.month)`
- **L92** `intconst` n -> n-1 — `4858259bd5c9`
  - `months = (reference_date.year - dob.year) * 12 + (reference_date.month - dob.month)`
  - `months = (reference_date.year - dob.year) * 11 + (reference_date.month - dob.month)`
- **L94** `intconst` n -> n+1 — `50146e3297bf`
  - `months -= 1`
  - `months -= 2`
- **L94** `intconst` n -> n-1 — `1e320fbef6e3`
  - `months -= 1`
  - `months -= 0`
- **L141** `compare` Eq -> NotEq — `74cfc5889930`
  - `latest_weight_recorded = select(WeightRecord.weight_kg).where(WeightRecord.animal_id == Animal.id).order_by(WeightRecord.date.desc(), WeightRecord.id.desc()).limit(1).correlate(Animal).scalar_subquery()`
  - `latest_weight_recorded = select(WeightRecord.weight_kg).where(WeightRecord.animal_id != Animal.id).order_by(WeightRecord.date.desc(), WeightRecord.id.desc()).limit(1).correlate(Animal).scalar_subquery()`
- **L159** `compare` LtE -> Lt — `b0a831e12ad5`
  - `birth_weight_as_of = case((effective_dob <= reference_date, Animal.birth_weight), else_=None)`
  - `birth_weight_as_of = case((effective_dob < reference_date, Animal.birth_weight), else_=None)`
- **L164** `compare` Eq -> NotEq — `d079b79b0725`
  - `latest_move = select(BucketMove.effective_date).where(BucketMove.animal_id == Animal.id).order_by(BucketMove.moved_at.desc(), BucketMove.id.desc()).limit(1).correlate(Animal).scalar_subquery()`
  - `latest_move = select(BucketMove.effective_date).where(BucketMove.animal_id != Animal.id).order_by(BucketMove.moved_at.desc(), BucketMove.id.desc()).limit(1).correlate(Animal).scalar_subquery()`
- **L191** `compare` GtE -> Gt — `2b3a1e42a15d`
  - `active_withdrawal = select(HealthEvent.id).where(HealthEvent.farm_id == farm.id, HealthEvent.animal_id == Animal.id, HealthEvent.withdrawal_until >= reference_date).correlate(Animal).exists()`
  - `active_withdrawal = select(HealthEvent.id).where(HealthEvent.farm_id == farm.id, HealthEvent.animal_id == Animal.id, HealthEvent.withdrawal_until > reference_date).correlate(Animal).exists()`
- **L240** `compare` LtE -> Lt — `75d562364922`
  - `resting_ready = bucket_started_local_date <= reference_date - timedelta(days=REBREED_AFTER_RESTING_DAYS)`
  - `resting_ready = bucket_started_local_date < reference_date - timedelta(days=REBREED_AFTER_RESTING_DAYS)`
- **L240** `binop` Sub -> Add — `6392932afedb`
  - `resting_ready = bucket_started_local_date <= reference_date - timedelta(days=REBREED_AFTER_RESTING_DAYS)`
  - `resting_ready = bucket_started_local_date <= reference_date + timedelta(days=REBREED_AFTER_RESTING_DAYS)`
- **L248** `compare` LtE -> Lt — `1bd86a63d0b9`
  - `breeding_rules = [and_(context.c.sex == 'F', context.c.current_bucket.in_([Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value]), context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.sex == 'F', context.c.current_bucket == Bucket.RESTING.value, resting_ready, context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.current_bucket == Bucket.PREGNANCY_EARLY.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=pregnancy_late_day)), and_(context.c.current_bucket == Bucket.PREGNANCY_LATE.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=due_window_day))]`
  - `breeding_rules = [and_(context.c.sex == 'F', context.c.current_bucket.in_([Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value]), context.c.effective_dob.is_not(None), context.c.effective_dob < age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.sex == 'F', context.c.current_bucket == Bucket.RESTING.value, resting_ready, context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.current_bucket == Bucket.PREGNANCY_EARLY.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=pregnancy_late_day)), and_(context.c.current_bucket == Bucket.PREGNANCY_LATE.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=due_window_day))]`
- **L249** `compare` GtE -> Gt — `c5fdd767393e`
  - `breeding_rules = [and_(context.c.sex == 'F', context.c.current_bucket.in_([Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value]), context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.sex == 'F', context.c.current_bucket == Bucket.RESTING.value, resting_ready, context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.current_bucket == Bucket.PREGNANCY_EARLY.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=pregnancy_late_day)), and_(context.c.current_bucket == Bucket.PREGNANCY_LATE.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=due_window_day))]`
  - `breeding_rules = [and_(context.c.sex == 'F', context.c.current_bucket.in_([Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value]), context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of > breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.sex == 'F', context.c.current_bucket == Bucket.RESTING.value, resting_ready, context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.current_bucket == Bucket.PREGNANCY_EARLY.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=pregnancy_late_day)), and_(context.c.current_bucket == Bucket.PREGNANCY_LATE.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=due_window_day))]`
- **L277** `compare` LtE -> Lt — `01b6ea9a52c2`
  - `market_rule = and_(context.c.sex == 'M', context.c.current_bucket == Bucket.MALE_KIDS.value, context.c.effective_dob.is_not(None), context.c.effective_dob <= add_months(reference_date, -MEAT_SALE_AGE_MONTHS[0]), context.c.latest_weight_as_of >= MEAT_SALE_WEIGHT_KG[0], context.c.has_active_withdrawal.is_(False))`
  - `market_rule = and_(context.c.sex == 'M', context.c.current_bucket == Bucket.MALE_KIDS.value, context.c.effective_dob.is_not(None), context.c.effective_dob < add_months(reference_date, -MEAT_SALE_AGE_MONTHS[0]), context.c.latest_weight_as_of >= MEAT_SALE_WEIGHT_KG[0], context.c.has_active_withdrawal.is_(False))`
- **L310** `ifexp` swap branches — `1133fe2eeb65`
  - `started_date = started_at if started_at is not None else business_date(row.created_at, farm.timezone)`
  - `started_date = business_date(row.created_at, farm.timezone) if started_at is not None else started_at`
- **L316** `intconst` n -> n+1 — `c7092d772fc5`
  - `bucket_days = max((reference_date - started_date).days, 0)`
  - `bucket_days = max((reference_date - started_date).days, 1)`
- **L333** `intconst` n -> n+1 — `5cf5e4878962`
  - `reason = f'{age} mo, {row.latest_weight_as_of:.1f} kg — market ready (window {MEAT_SALE_AGE_MONTHS[0]}–{MEAT_SALE_AGE_MONTHS[1]} mo, {MEAT_SALE_WEIGHT_KG[0]:.0f}–{MEAT_SALE_WEIGHT_KG[1]:.0f} kg)'`
  - `reason = f'{age} mo, {row.latest_weight_as_of:.1f} kg — market ready (window {MEAT_SALE_AGE_MONTHS[1]}–{MEAT_SALE_AGE_MONTHS[1]} mo, {MEAT_SALE_WEIGHT_KG[0]:.0f}–{MEAT_SALE_WEIGHT_KG[1]:.0f} kg)'`

### `app/services/feeding.py` (17 survivors)

- **L132** `intconst` n -> n+1 — `e7d9aae284f4`
  - `weighted = [(shift, total_units * int(Decimal(str(pct)) * 100)) for shift, pct in SHIFT_SPLIT.items()]`
  - `weighted = [(shift, total_units * int(Decimal(str(pct)) * 101)) for shift, pct in SHIFT_SPLIT.items()]`
- **L138** `intconst` n -> n+1 — `c33b03cc4105`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 100), item[0]))`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 100), item[1]))`
- **L138** `intconst` n -> n+1 — `123b0575f23f`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 100), item[0]))`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 101), item[0]))`
- **L138** `intconst` n -> n-1 — `78288a3f5c07`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 100), item[0]))`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 99), item[0]))`
- **L186** `intconst` n -> n+1 — `9e479f993361`
  - `scaled = Decimal(str(mean_weight)) * Decimal(str(class_pct)) / Decimal(100)`
  - `scaled = Decimal(str(mean_weight)) * Decimal(str(class_pct)) / Decimal(101)`
- **L186** `intconst` n -> n-1 — `86a734d41a63`
  - `scaled = Decimal(str(mean_weight)) * Decimal(str(class_pct)) / Decimal(100)`
  - `scaled = Decimal(str(mean_weight)) * Decimal(str(class_pct)) / Decimal(99)`
- **L236** `boolop` Or -> And — `c496d14f81ed`
  - `ref = ref or today(farm.timezone)`
  - `ref = ref and today(farm.timezone)`
- **L265** `intconst` n -> n+1 — `dc72e295ad74`
  - `bucket_days = func.greatest(ref - bucket_started_date, 0)`
  - `bucket_days = func.greatest(ref - bucket_started_date, 1)`
- **L267** `intconst` n -> n+1 — `16e1d0f23dc3`
  - `age_days = func.coalesce(ref - effective_dob, 999)`
  - `age_days = func.coalesce(ref - effective_dob, 1000)`
- **L267** `intconst` n -> n-1 — `a43454cd5d45`
  - `age_days = func.coalesce(ref - effective_dob, 999)`
  - `age_days = func.coalesce(ref - effective_dob, 998)`
- **L280** `compare` LtE -> Lt — `84f48a785104`
  - `is_dependent_kid = and_(Animal.dam_id.is_not(None), age_days <= weaning_days, exists(select(1).where(dam_animal.id == Animal.dam_id, dam_animal.farm_id == farm.id, dam_animal.status == AnimalStatus.ACTIVE.value, dam_animal.current_bucket == Bucket.RECOVERY.value)))`
  - `is_dependent_kid = and_(Animal.dam_id.is_not(None), age_days < weaning_days, exists(select(1).where(dam_animal.id == Animal.dam_id, dam_animal.farm_id == farm.id, dam_animal.status == AnimalStatus.ACTIVE.value, dam_animal.current_bucket == Bucket.RECOVERY.value)))`
- **L283** `compare` Eq -> NotEq — `a7cd7edbc9e8`
  - `is_dependent_kid = and_(Animal.dam_id.is_not(None), age_days <= weaning_days, exists(select(1).where(dam_animal.id == Animal.dam_id, dam_animal.farm_id == farm.id, dam_animal.status == AnimalStatus.ACTIVE.value, dam_animal.current_bucket == Bucket.RECOVERY.value)))`
  - `is_dependent_kid = and_(Animal.dam_id.is_not(None), age_days <= weaning_days, exists(select(1).where(dam_animal.id != Animal.dam_id, dam_animal.farm_id == farm.id, dam_animal.status == AnimalStatus.ACTIVE.value, dam_animal.current_bucket == Bucket.RECOVERY.value)))`
- **L407** `compare` LtE -> Lt — `7a4e2108b180`
  - `weight_source = select(Animal.current_bucket.label('bucket'), latest_weight.c.weight_kg.label('weight_kg')).outerjoin(latest_weight, true()).where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value, or_(func.coalesce(Animal.date_of_birth, Animal.estimated_dob).is_(None), func.coalesce(Animal.date_of_birth, Animal.estimated_dob) <= ref - timedelta(days=GOAT_PROFILE.weaning_days))).subquery('feeding_weights')`
  - `weight_source = select(Animal.current_bucket.label('bucket'), latest_weight.c.weight_kg.label('weight_kg')).outerjoin(latest_weight, true()).where(Animal.farm_id == farm.id, Animal.status == AnimalStatus.ACTIVE.value, or_(func.coalesce(Animal.date_of_birth, Animal.estimated_dob).is_(None), func.coalesce(Animal.date_of_birth, Animal.estimated_dob) < ref - timedelta(days=GOAT_PROFILE.weaning_days))).subquery('feeding_weights')`
- **L491** `intconst` n -> n+1 — `f3591c194843`
  - `lines.append({'bucket': bucket_code, 'recipe_code': recipe_code, 'recipe_name': RECIPE_DISPLAY.get(recipe_code, recipe_code), 'heads': heads, 'kg_per_head': kg_per_head, 'daily_kg': daily_kg, 'shifts': [{'shift': shift.value, 'pct': int(pct * 100), 'kg': shift_quantities[shift], 'time': SHIFT_TIMES[shift.value]} for shift, pct in SHIFT_SPLIT.items()], 'creep_band': band if recipe_code == 'CREEP' else None, 'basis': basis, 'mean_weight_kg': mean_weight_kg, 'note': note})`
  - `lines.append({'bucket': bucket_code, 'recipe_code': recipe_code, 'recipe_name': RECIPE_DISPLAY.get(recipe_code, recipe_code), 'heads': heads, 'kg_per_head': kg_per_head, 'daily_kg': daily_kg, 'shifts': [{'shift': shift.value, 'pct': int(pct * 101), 'kg': shift_quantities[shift], 'time': SHIFT_TIMES[shift.value]} for shift, pct in SHIFT_SPLIT.items()], 'creep_band': band if recipe_code == 'CREEP' else None, 'basis': basis, 'mean_weight_kg': mean_weight_kg, 'note': note})`
- **L547** `boolconst` True -> False — `e5144d09fb35`
  - `grams_by_ingredient = dict(zip(weights_by_ingredient, allocated_grams, strict=True))`
  - `grams_by_ingredient = dict(zip(weights_by_ingredient, allocated_grams, strict=False))`
- **L564** `intconst` n -> n+1 — `0272d7b3f8a7`
  - `on_hand = Decimal(str(item.qty_on_hand)) if item else Decimal(0)`
  - `on_hand = Decimal(str(item.qty_on_hand)) if item else Decimal(1)`
- **L717** `intconst` n -> n+1 — `2693170ea2ea`
  - `available = Decimal(str(stock.qty_on_hand)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP) if stock else Decimal(0)`
  - `available = Decimal(str(stock.qty_on_hand)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP) if stock else Decimal(1)`

### `app/services/finance.py` (4 survivors)

- **L68** `intconst` n -> n+1 — `5f3d7dd05f6a`
  - `first_month = add_months(current_month, -(n_months - 1))`
  - `first_month = add_months(current_month, -(n_months - 2))`
- **L146** `intconst` n -> n+1 — `f533adcf60cb`
  - `row['net'] = round(row['income'] - row['expense'], 2)`
  - `row['net'] = round(row['income'] - row['expense'], 3)`
- **L507** `ifexp` swap branches — `2c370bc70519`
  - `purchase_cost = ledger_purchase if ledger_purchase else animal.purchase_price or Decimal('0.00')`
  - `purchase_cost = animal.purchase_price or Decimal('0.00') if ledger_purchase else ledger_purchase`
- **L586** `intconst` n -> n+1 — `d188b6d86d21`
  - `after_last_month = add_months(current_month, 1)`
  - `after_last_month = add_months(current_month, 2)`

### `app/services/health.py` (19 survivors)

- **L61** `binop` Sub -> Add — `f33f648003c8`
  - `windows = range(len(words) - width + 1)`
  - `windows = range(len(words) + width + 1)`
- **L61** `intconst` n -> n+1 — `fb884352e9ad`
  - `windows = range(len(words) - width + 1)`
  - `windows = range(len(words) - width + 2)`
- **L84** `intconst` n -> n+1 — `0568e9b5a08e`
  - `phrase = phrase[closing + 1:]`
  - `phrase = phrase[closing + 2:]`
- **L180** `intconst` n -> n-1 — `8eebcd1901a8`
  - `aliases.append(abbrev_match.group(1))`
  - `aliases.append(abbrev_match.group(0))`
- **L273** `boolop` Or -> And — `6a84974ec55a`
  - `action = MovementRestrictionAction(farm_id=animal.farm_id, animal_id=animal.id, restriction_version=animal.restriction_version, action='PLACED', acted_at=acted_at or utcnow(), acted_by_id=acted_by_id, action_reference=action_reference, disease_target=target, health_event_id=health_event_id)`
  - `action = MovementRestrictionAction(farm_id=animal.farm_id, animal_id=animal.id, restriction_version=animal.restriction_version, action='PLACED', acted_at=acted_at and utcnow(), acted_by_id=acted_by_id, action_reference=action_reference, disease_target=target, health_event_id=health_event_id)`
- **L440** `boolop` Or -> And — `4d215c966ef9`
  - `label = (product_name or disease_target or event_type).strip() or event_type`
  - `label = (product_name or disease_target or event_type).strip() and event_type`
- **L440** `boolop` Or -> And — `1530a5347758`
  - `label = (product_name or disease_target or event_type).strip() or event_type`
  - `label = (product_name and disease_target and event_type).strip() or event_type`
- **L441** `ifexp` swap branches — `ce0b436fdaaf`
  - `head = f'for {animal_count} animal' + ('' if animal_count == 1 else 's')`
  - `head = f'for {animal_count} animal' + ('s' if animal_count == 1 else '')`
- **L441** `compare` Eq -> NotEq — `d3efc07edf8e`
  - `head = f'for {animal_count} animal' + ('' if animal_count == 1 else 's')`
  - `head = f'for {animal_count} animal' + ('' if animal_count != 1 else 's')`
- **L441** `intconst` n -> n+1 — `f1f8df5458f4`
  - `head = f'for {animal_count} animal' + ('' if animal_count == 1 else 's')`
  - `head = f'for {animal_count} animal' + ('' if animal_count == 2 else 's')`
- **L441** `intconst` n -> n-1 — `8bf162e39753`
  - `head = f'for {animal_count} animal' + ('' if animal_count == 1 else 's')`
  - `head = f'for {animal_count} animal' + ('' if animal_count == 0 else 's')`
- **L460** `intconst` n -> n-1 — `2aeb7812ae8a`
  - `LEGACY_SCHEDULE_SCAN_LIMIT = 500`
  - `LEGACY_SCHEDULE_SCAN_LIMIT = 499`
- **L552** `ifexp` swap branches — `1fd26d285196`
  - `reference_date = today(farm.timezone) if farm is not None else today()`
  - `reference_date = today() if farm is not None else today(farm.timezone)`
- **L552** `compare` IsNot -> Is — `e3f29cc31905`
  - `reference_date = today(farm.timezone) if farm is not None else today()`
  - `reference_date = today(farm.timezone) if farm is None else today()`
- **L580** `intconst` n -> n+1 — `68435aa347bf`
  - `latest = select(literal(template.id).label('template_id'), HealthEvent.id.label('event_id'), HealthEvent.date.label('event_date'), HealthEvent.next_due_date, HealthEvent.next_due_authority, literal(False).label('is_primary')).where(HealthEvent.animal_id == animal.id, HealthEvent.schedule_template_id == template.id, HealthEvent.type.in_([HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value])).order_by(HealthEvent.date.desc(), HealthEvent.id.desc()).limit(2).subquery()`
  - `latest = select(literal(template.id).label('template_id'), HealthEvent.id.label('event_id'), HealthEvent.date.label('event_date'), HealthEvent.next_due_date, HealthEvent.next_due_authority, literal(False).label('is_primary')).where(HealthEvent.animal_id == animal.id, HealthEvent.schedule_template_id == template.id, HealthEvent.type.in_([HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value])).order_by(HealthEvent.date.desc(), HealthEvent.id.desc()).limit(3).subquery()`
- **L669** `intconst` n -> n+1 — `ac1399ff8123`
  - `del matches[2:]`
  - `del matches[3:]`
- **L711** `intconst` n -> n-1 — `e754efb624fb`
  - `booster_missed = authoritative_next_due is None and len(done) == 1 and (booster_due is not None) and (booster_due < reference_date)`
  - `booster_missed = authoritative_next_due is None and len(done) == 0 and (booster_due is not None) and (booster_due < reference_date)`
- **L713** `compare` Lt -> LtE — `c35c2af0f5f2`
  - `booster_missed = authoritative_next_due is None and len(done) == 1 and (booster_due is not None) and (booster_due < reference_date)`
  - `booster_missed = authoritative_next_due is None and len(done) == 1 and (booster_due is not None) and (booster_due <= reference_date)`
- **L723** `compare` GtE -> Gt — `5dabe048cb23`
  - `status = 'DONE' if next_due is not None and next_due >= reference_date else 'OVERDUE'`
  - `status = 'DONE' if next_due is not None and next_due > reference_date else 'OVERDUE'`

### `app/services/idempotency.py` (1 survivors)

- **L395** `loopjump` break -> continue — `7551357dcdfc`
  - `break`
  - `continue`

### `app/services/kidding.py` (4 survivors)

- **L209** `intconst` n -> n+1 — `4131c88eb6e5`
  - `suffix = f'-A{secrets.token_hex(6)}'`
  - `suffix = f'-A{secrets.token_hex(7)}'`
- **L209** `intconst` n -> n-1 — `905b55b6dd44`
  - `suffix = f'-A{secrets.token_hex(6)}'`
  - `suffix = f'-A{secrets.token_hex(5)}'`
- **L353** `boolop` And -> Or — `59215b034b94`
  - `mortality_dates = [kid['mortality_reported_at'] for kid in kids if kid['status'] == KidStatus.DIED.value and kid['mortality_reported_at'] is not None]`
  - `mortality_dates = [kid['mortality_reported_at'] for kid in kids if kid['status'] == KidStatus.DIED.value or kid['mortality_reported_at'] is not None]`
- **L551** `loopjump` continue -> break — `acb78c1558b5`
  - `continue`
  - `break`

### `app/services/notifications/service.py` (15 survivors)

- **L114** `binop` Add -> Sub — `0f847e7deb86`
  - `logger.warning('notification transport failed (attempt %d/%d), retrying: %s', attempt + 1, attempts, exc)`
  - `logger.warning('notification transport failed (attempt %d/%d), retrying: %s', attempt - 1, attempts, exc)`
- **L114** `intconst` n -> n+1 — `e788ad84412c`
  - `logger.warning('notification transport failed (attempt %d/%d), retrying: %s', attempt + 1, attempts, exc)`
  - `logger.warning('notification transport failed (attempt %d/%d), retrying: %s', attempt + 2, attempts, exc)`
- **L114** `intconst` n -> n-1 — `5bf289676d75`
  - `logger.warning('notification transport failed (attempt %d/%d), retrying: %s', attempt + 1, attempts, exc)`
  - `logger.warning('notification transport failed (attempt %d/%d), retrying: %s', attempt + 0, attempts, exc)`
- **L191** `intconst` n -> n+1 — `5855ab2f2b09`
  - `db.add(NotificationLog(farm_id=farm.id, recipient_id=recipient.id, alert_class=alert_class, payload_hash=digest, local_date=local_date, status=status, provider_message_id=message_id, error=None if error is None else redact_phone_numbers(error)[:500]))`
  - `db.add(NotificationLog(farm_id=farm.id, recipient_id=recipient.id, alert_class=alert_class, payload_hash=digest, local_date=local_date, status=status, provider_message_id=message_id, error=None if error is None else redact_phone_numbers(error)[:501]))`
- **L191** `intconst` n -> n-1 — `175a2c196b58`
  - `db.add(NotificationLog(farm_id=farm.id, recipient_id=recipient.id, alert_class=alert_class, payload_hash=digest, local_date=local_date, status=status, provider_message_id=message_id, error=None if error is None else redact_phone_numbers(error)[:500]))`
  - `db.add(NotificationLog(farm_id=farm.id, recipient_id=recipient.id, alert_class=alert_class, payload_hash=digest, local_date=local_date, status=status, provider_message_id=message_id, error=None if error is None else redact_phone_numbers(error)[:499]))`
- **L289** `boolop` Or -> And — `0da342cbb7d1`
  - `now = now_local or datetime.now(ZoneInfo(farm.timezone))`
  - `now = now_local and datetime.now(ZoneInfo(farm.timezone))`
- **L350** `loopjump` continue -> break — `c4f64b64638b`
  - `continue`
  - `break`
- **L496** `intconst` n -> n+1 — `c4345a4493cd`
  - `names = ', '.join((str(row[0]) for row in rows[:5]))`
  - `names = ', '.join((str(row[0]) for row in rows[:6]))`
- **L499** `ifexp` swap branches — `810d80716d35`
  - `message = f"Herdly: {len(rows)} feed items below reorder level ({names}{('…' if len(rows) > 5 else '')}). Order feed."`
  - `message = f"Herdly: {len(rows)} feed items below reorder level ({names}{('' if len(rows) > 5 else '…')}). Order feed."`
- **L499** `compare` Gt -> GtE — `f654ef91d337`
  - `message = f"Herdly: {len(rows)} feed items below reorder level ({names}{('…' if len(rows) > 5 else '')}). Order feed."`
  - `message = f"Herdly: {len(rows)} feed items below reorder level ({names}{('…' if len(rows) >= 5 else '')}). Order feed."`
- **L499** `intconst` n -> n+1 — `82cc398a488d`
  - `message = f"Herdly: {len(rows)} feed items below reorder level ({names}{('…' if len(rows) > 5 else '')}). Order feed."`
  - `message = f"Herdly: {len(rows)} feed items below reorder level ({names}{('…' if len(rows) > 6 else '')}). Order feed."`
- **L499** `intconst` n -> n-1 — `227421da52d5`
  - `message = f"Herdly: {len(rows)} feed items below reorder level ({names}{('…' if len(rows) > 5 else '')}). Order feed."`
  - `message = f"Herdly: {len(rows)} feed items below reorder level ({names}{('…' if len(rows) > 4 else '')}). Order feed."`
- **L542** `compare` GtE -> Gt — `295dd345121c`
  - `critical = [due for due in rows if (reference - due).days >= 3]`
  - `critical = [due for due in rows if (reference - due).days > 3]`
- **L542** `intconst` n -> n+1 — `6ad5682b4311`
  - `critical = [due for due in rows if (reference - due).days >= 3]`
  - `critical = [due for due in rows if (reference - due).days >= 4]`
- **L542** `intconst` n -> n-1 — `178dbce2f454`
  - `critical = [due for due in rows if (reference - due).days >= 3]`
  - `critical = [due for due in rows if (reference - due).days >= 2]`

### `app/services/purchases.py` (1 survivors)

- **L218** `boolconst` True -> False — `459b822c15ac`
  - `db.add_all([WeightRecord(animal_id=animal.id, date=batch_date, weight_kg=weight, notes='Arrival weight (individual)', created_by_id=created_by_id) for animal, weight in zip(animals, individual_weights_kg, strict=True)])`
  - `db.add_all([WeightRecord(animal_id=animal.id, date=batch_date, weight_kg=weight, notes='Arrival weight (individual)', created_by_id=created_by_id) for animal, weight in zip(animals, individual_weights_kg, strict=False)])`

### `app/services/screening/detect.py` (7 survivors)

- **L26** `intconst` n -> n+1 — `6a81929f6e72`
  - `MIN_BOX_SIZE = 40`
  - `MIN_BOX_SIZE = 41`
- **L26** `intconst` n -> n-1 — `cc81d59ebb8c`
  - `MIN_BOX_SIZE = 40`
  - `MIN_BOX_SIZE = 39`
- **L28** `intconst` n -> n+1 — `b28df615f54a`
  - `MAX_DETECTED_GOATS = 20`
  - `MAX_DETECTED_GOATS = 21`
- **L28** `intconst` n -> n-1 — `86d0dd7eafa0`
  - `MAX_DETECTED_GOATS = 20`
  - `MAX_DETECTED_GOATS = 19`
- **L50** `boolconst` True -> False — `760630310c66`
  - `model_config = ConfigDict(str_strip_whitespace=True)`
  - `model_config = ConfigDict(str_strip_whitespace=False)`
- **L52** `intconst` n -> n-1 — `e595fc1c7221`
  - `box: list[int] = Field(min_length=4, max_length=4)`
  - `box: list[int] = Field(min_length=3, max_length=4)`
- **L103** `loopjump` continue -> break — `4ad3ecd9338b`
  - `continue`
  - `break`

### `app/services/screening/gate.py` (11 survivors)

- **L23** `intconst` n -> n+1 — `f6e1267e9416`
  - `MAX_OBSERVATIONS = 8`
  - `MAX_OBSERVATIONS = 9`
- **L23** `intconst` n -> n-1 — `038a40211a59`
  - `MAX_OBSERVATIONS = 8`
  - `MAX_OBSERVATIONS = 7`
- **L66** `boolconst` True -> False — `59c0b8e29aab`
  - `model_config = ConfigDict(str_strip_whitespace=True)`
  - `model_config = ConfigDict(str_strip_whitespace=False)`
- **L68** `intconst` n -> n-1 — `6371374d1302`
  - `region: str = Field(min_length=1, max_length=40)`
  - `region: str = Field(min_length=0, max_length=40)`
- **L68** `intconst` n -> n+1 — `67119a9a744b`
  - `region: str = Field(min_length=1, max_length=40)`
  - `region: str = Field(min_length=1, max_length=41)`
- **L68** `intconst` n -> n-1 — `7ee73c07b412`
  - `region: str = Field(min_length=1, max_length=40)`
  - `region: str = Field(min_length=1, max_length=39)`
- **L69** `intconst` n -> n+1 — `0252daf4cd30`
  - `label: str = Field(min_length=1, max_length=80)`
  - `label: str = Field(min_length=2, max_length=80)`
- **L69** `intconst` n -> n-1 — `ceded7dd9c39`
  - `label: str = Field(min_length=1, max_length=80)`
  - `label: str = Field(min_length=0, max_length=80)`
- **L71** `intconst` n -> n-1 — `b95e20f1914e`
  - `note: str | None = Field(default=None, max_length=500)`
  - `note: str | None = Field(default=None, max_length=499)`
- **L79** `boolconst` False -> True — `6f7f48ed007e`
  - `quality_problem: bool = False`
  - `quality_problem: bool = True`
- **L122** `boolconst` True -> False — `3e39c1dad224`
  - `in_string = True`
  - `in_string = False`

### `app/services/screening/images.py` (12 survivors)

- **L22** `intconst` n -> n+1 — `ad438a186712`
  - `MAX_DECODED_PIXELS = 25000000`
  - `MAX_DECODED_PIXELS = 25000001`
- **L23** `intconst` n -> n+1 — `018fe1775fb8`
  - `MAX_DECODED_EDGE_PX = 10000`
  - `MAX_DECODED_EDGE_PX = 10001`
- **L23** `intconst` n -> n-1 — `af1f536d79f2`
  - `MAX_DECODED_EDGE_PX = 10000`
  - `MAX_DECODED_EDGE_PX = 9999`
- **L24** `intconst` n -> n+1 — `905025b91b3b`
  - `JPEG_QUALITY = 85`
  - `JPEG_QUALITY = 86`
- **L24** `intconst` n -> n-1 — `4ad5c6be2c3f`
  - `JPEG_QUALITY = 85`
  - `JPEG_QUALITY = 84`
- **L106** `boolconst` True -> False — `d16f2452a35a`
  - `rgb.save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=True)`
  - `rgb.save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=False)`
- **L158** `intconst` n -> n+1 — `f40bcac6044b`
  - `left = max(0, round(box.x / 1000 * width - margin_x))`
  - `left = max(1, round(box.x / 1000 * width - margin_x))`
- **L159** `intconst` n -> n+1 — `0e0c282840d5`
  - `top = max(0, round(box.y / 1000 * height - margin_y))`
  - `top = max(1, round(box.y / 1000 * height - margin_y))`
- **L159** `intconst` n -> n+1 — `1b2388219e7e`
  - `top = max(0, round(box.y / 1000 * height - margin_y))`
  - `top = max(0, round(box.y / 1001 * height - margin_y))`
- **L159** `intconst` n -> n-1 — `5e08f22821c8`
  - `top = max(0, round(box.y / 1000 * height - margin_y))`
  - `top = max(0, round(box.y / 999 * height - margin_y))`
- **L161** `intconst` n -> n-1 — `d22df59f78a2`
  - `bottom = min(height, round((box.y + box.h) / 1000 * height + margin_y))`
  - `bottom = min(height, round((box.y + box.h) / 999 * height + margin_y))`
- **L170** `boolconst` True -> False — `ddac4ddaabdc`
  - `cropped.save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=True)`
  - `cropped.save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=False)`

### `app/services/screening/pipeline.py` (29 survivors)

- **L145** `binop` Mult -> Div — `d467531245c2`
  - `DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 30 * 60`
  - `DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 30 / 60`
- **L145** `intconst` n -> n+1 — `a378e466294b`
  - `DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 30 * 60`
  - `DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 31 * 60`
- **L145** `intconst` n -> n-1 — `761c4ea7963c`
  - `DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 30 * 60`
  - `DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 29 * 60`
- **L145** `intconst` n -> n-1 — `115795b2ffaa`
  - `DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 30 * 60`
  - `DEFAULT_STALE_PROCESSING_AFTER_SECONDS = 30 * 59`
- **L153** `intconst` n -> n+1 — `820d34a95a55`
  - `ERROR_RETRY_AFTER = dt.timedelta(hours=1)`
  - `ERROR_RETRY_AFTER = dt.timedelta(hours=2)`
- **L160** `intconst` n -> n+1 — `007a9d8ba281`
  - `MAX_SCREENING_ATTEMPTS = 5`
  - `MAX_SCREENING_ATTEMPTS = 6`
- **L160** `intconst` n -> n-1 — `4eeb0b1ee88a`
  - `MAX_SCREENING_ATTEMPTS = 5`
  - `MAX_SCREENING_ATTEMPTS = 4`
- **L166** `intconst` n -> n+1 — `690ea2088b07`
  - `_CLAIM_CANDIDATE_WINDOW = 2000`
  - `_CLAIM_CANDIDATE_WINDOW = 2001`
- **L166** `intconst` n -> n-1 — `459187ec49ec`
  - `_CLAIM_CANDIDATE_WINDOW = 2000`
  - `_CLAIM_CANDIDATE_WINDOW = 1999`
- **L261** `intconst` n -> n+1 — `1bf10c74f081`
  - `claimed: int = 0`
  - `claimed: int = 1`
- **L310** `compare` Lt -> LtE — `93ea3399cc92`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PENDING.value, ScreeningImage.upload_token.is_not(None), ScreeningImage.created_at < now - abandoned_after).order_by(ScreeningImage.created_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=True)`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PENDING.value, ScreeningImage.upload_token.is_not(None), ScreeningImage.created_at <= now - abandoned_after).order_by(ScreeningImage.created_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=True)`
- **L314** `boolconst` True -> False — `b4d843f69e5d`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PENDING.value, ScreeningImage.upload_token.is_not(None), ScreeningImage.created_at < now - abandoned_after).order_by(ScreeningImage.created_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=True)`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PENDING.value, ScreeningImage.upload_token.is_not(None), ScreeningImage.created_at < now - abandoned_after).order_by(ScreeningImage.created_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=False)`
- **L356** `compare` Lt -> LtE — `ae3a997b9c63`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PROCESSING.value, ScreeningImage.screening_attempts >= MAX_SCREENING_ATTEMPTS, ScreeningImage.updated_at < now - stale_after).order_by(ScreeningImage.updated_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=True)`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PROCESSING.value, ScreeningImage.screening_attempts >= MAX_SCREENING_ATTEMPTS, ScreeningImage.updated_at <= now - stale_after).order_by(ScreeningImage.updated_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=True)`
- **L356** `binop` Sub -> Add — `f56b9c001eb2`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PROCESSING.value, ScreeningImage.screening_attempts >= MAX_SCREENING_ATTEMPTS, ScreeningImage.updated_at < now - stale_after).order_by(ScreeningImage.updated_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=True)`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PROCESSING.value, ScreeningImage.screening_attempts >= MAX_SCREENING_ATTEMPTS, ScreeningImage.updated_at < now + stale_after).order_by(ScreeningImage.updated_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=True)`
- **L360** `boolconst` True -> False — `42378c8e01f1`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PROCESSING.value, ScreeningImage.screening_attempts >= MAX_SCREENING_ATTEMPTS, ScreeningImage.updated_at < now - stale_after).order_by(ScreeningImage.updated_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=True)`
  - `candidates = select(ScreeningImage.id).where(ScreeningImage.status == ScreeningImageStatus.PROCESSING.value, ScreeningImage.screening_attempts >= MAX_SCREENING_ATTEMPTS, ScreeningImage.updated_at < now - stale_after).order_by(ScreeningImage.updated_at, ScreeningImage.id).limit(_EXPIRED_UPLOAD_BATCH_SIZE).with_for_update(skip_locked=False)`
- **L486** `compare` GtE -> Gt — `5cb2e847cef1`
  - `over_budget_parts = [select(ScreeningRun.farm_id).join(Farm, Farm.id == ScreeningRun.farm_id).where(ScreeningRun.created_at >= day_start, Farm.timezone == timezone_name).group_by(ScreeningRun.farm_id).having(func.count() > budget - claim_reservation) for timezone_name, day_start in day_starts.items()]`
  - `over_budget_parts = [select(ScreeningRun.farm_id).join(Farm, Farm.id == ScreeningRun.farm_id).where(ScreeningRun.created_at > day_start, Farm.timezone == timezone_name).group_by(ScreeningRun.farm_id).having(func.count() > budget - claim_reservation) for timezone_name, day_start in day_starts.items()]`
- **L504** `compare` GtE -> Gt — `095f8f031ea2`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at > pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
- **L508** `compare` LtE -> Lt — `9c12c620e9b0`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at < now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
- **L513** `compare` Lt -> LtE — `6200eb0b1c27`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at <= stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
- **L517** `compare` Lt -> LtE — `2e28e584540a`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at <= retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
- **L524** `binop` BitAnd -> BitOr — `bbe68a62cb73`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | ((ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) | partial_crop_error))`
- **L524** `binop` BitAnd -> BitOr — `fd33907f43d4`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | (ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) & (ScreeningImage.updated_at < retry_horizon) & partial_crop_error)`
  - `eligible = (ScreeningImage.screening_attempts < MAX_SCREENING_ATTEMPTS) & budget_ok & ((ScreeningImage.status == ScreeningImageStatus.PENDING.value) & or_(ScreeningImage.upload_token.is_(None), ScreeningImage.created_at >= pending_horizon) & or_(ScreeningImage.next_attempt_at.is_(None), ScreeningImage.next_attempt_at <= now) | (ScreeningImage.status == ScreeningImageStatus.PROCESSING.value) & (ScreeningImage.updated_at < stale_horizon) | (ScreeningImage.status == ScreeningImageStatus.ERROR.value) & (ScreeningImage.updated_at < retry_horizon) | ((ScreeningImage.status == ScreeningImageStatus.FLAGGED.value) | (ScreeningImage.updated_at < retry_horizon)) & partial_crop_error)`
- **L656** `boolconst` False -> True — `5c843ecf0109`
  - `identities_expired = False`
  - `identities_expired = True`
- **L663** `boolconst` False -> True — `82e5c823ad95`
  - `identities_expired = False`
  - `identities_expired = True`
- **L693** `boolconst` True -> False — `2ea6f54f8d07`
  - `identities_expired = True`
  - `identities_expired = False`
- **L704** `boolop` Or -> And — `5f4a33fbd2e0`
  - `image.error = f"{image.error or 'screening failed'}; terminal after {attempts} attempts"`
  - `image.error = f"{image.error and 'screening failed'}; terminal after {attempts} attempts"`
- **L717** `boolconst` True -> False — `a3dd426438f2`
  - `identities_expired = True`
  - `identities_expired = False`
- **L1216** `intconst` n -> n+1 — `0cc5bdb5422c`
  - `image.screening_attempts = max(0, image.screening_attempts - 1)`
  - `image.screening_attempts = max(0, image.screening_attempts - 2)`
- **L1432** `boolop` Or -> And — `a6bf7281536e`
  - `captured = image.captured_date or business_today`
  - `captured = image.captured_date and business_today`

### `app/services/screening/providers.py` (7 survivors)

- **L126** `compare` Is -> IsNot — `3e7520462cfe`
  - `self._owns_client = client is None`
  - `self._owns_client = client is not None`
- **L136** `intconst` n -> n+1 — `7b0c42e2cf36`
  - `body = {'model': self.model, 'max_tokens': 1024, 'system': system_prompt, 'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(image_jpeg).decode('ascii')}}, {'type': 'text', 'text': 'Analyze this photo. JSON only.'}]}]}`
  - `body = {'model': self.model, 'max_tokens': 1025, 'system': system_prompt, 'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(image_jpeg).decode('ascii')}}, {'type': 'text', 'text': 'Analyze this photo. JSON only.'}]}]}`
- **L136** `intconst` n -> n-1 — `3932d9bb4e4d`
  - `body = {'model': self.model, 'max_tokens': 1024, 'system': system_prompt, 'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(image_jpeg).decode('ascii')}}, {'type': 'text', 'text': 'Analyze this photo. JSON only.'}]}]}`
  - `body = {'model': self.model, 'max_tokens': 1023, 'system': system_prompt, 'messages': [{'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(image_jpeg).decode('ascii')}}, {'type': 'text', 'text': 'Analyze this photo. JSON only.'}]}]}`
- **L170** `boolop` And -> Or — `d9e6bc6cefa4`
  - `text = ''.join((block.get('text', '') for block in payload.get('content', []) if isinstance(block, dict) and block.get('type') == 'text'))`
  - `text = ''.join((block.get('text', '') for block in payload.get('content', []) if isinstance(block, dict) or block.get('type') == 'text'))`
- **L219** `compare` Is -> IsNot — `b9215c1aed51`
  - `self._owns_client = client is None`
  - `self._owns_client = client is not None`
- **L230** `intconst` n -> n+1 — `b1b715892438`
  - `body = {'model': self.model, 'max_completion_tokens': 1024, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Analyze this photo. JSON only.'}, {'type': 'image_url', 'image_url': {'url': data_url}}]}], 'response_format': {'type': 'json_object'}}`
  - `body = {'model': self.model, 'max_completion_tokens': 1025, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Analyze this photo. JSON only.'}, {'type': 'image_url', 'image_url': {'url': data_url}}]}], 'response_format': {'type': 'json_object'}}`
- **L230** `intconst` n -> n-1 — `ad58fe176784`
  - `body = {'model': self.model, 'max_completion_tokens': 1024, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Analyze this photo. JSON only.'}, {'type': 'image_url', 'image_url': {'url': data_url}}]}], 'response_format': {'type': 'json_object'}}`
  - `body = {'model': self.model, 'max_completion_tokens': 1023, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Analyze this photo. JSON only.'}, {'type': 'image_url', 'image_url': {'url': data_url}}]}], 'response_format': {'type': 'json_object'}}`

### `app/services/screening/rotation.py` (1 survivors)

- **L80** `binop` Add -> Sub — `3fc9bd265609`
  - `candidate = self._providers[(on.toordinal() + step) % len(self._providers)]`
  - `candidate = self._providers[(on.toordinal() - step) % len(self._providers)]`

### `app/services/screening/s3.py` (7 survivors)

- **L27** `intconst` n -> n+1 — `1a2b955591b2`
  - `_PRESIGN_EXPIRY_CAP_SECONDS = 86400`
  - `_PRESIGN_EXPIRY_CAP_SECONDS = 86401`
- **L27** `intconst` n -> n-1 — `156f5150c297`
  - `_PRESIGN_EXPIRY_CAP_SECONDS = 86400`
  - `_PRESIGN_EXPIRY_CAP_SECONDS = 86399`
- **L34** `intconst` n -> n+1 — `7d4464494ae8`
  - `_S3_CONNECT_TIMEOUT_SECONDS = 5`
  - `_S3_CONNECT_TIMEOUT_SECONDS = 6`
- **L36** `intconst` n -> n+1 — `9669a5be8298`
  - `_S3_TOTAL_MAX_ATTEMPTS = 1`
  - `_S3_TOTAL_MAX_ATTEMPTS = 2`
- **L37** `intconst` n -> n+1 — `3b9864039be0`
  - `_S3_DOWNLOAD_DEADLINE_SECONDS = 20`
  - `_S3_DOWNLOAD_DEADLINE_SECONDS = 21`
- **L248** `intconst` n -> n+1 — `328ae4fd39ba`
  - `content_length = int(response.get('ContentLength', 0))`
  - `content_length = int(response.get('ContentLength', 1))`
- **L263** `binop` Sub -> Add — `60cd6fad1e9f`
  - `chunk = cast(bytes, body.read(min(_DOWNLOAD_CHUNK_BYTES, max_bytes - read + 1)))`
  - `chunk = cast(bytes, body.read(min(_DOWNLOAD_CHUNK_BYTES, max_bytes + read + 1)))`

### `app/services/screening/specialists.py` (9 survivors)

- **L21** `intconst` n -> n+1 — `cedb52f0a04c`
  - `MAX_SPECIALIST_CONDITIONS = 5`
  - `MAX_SPECIALIST_CONDITIONS = 6`
- **L21** `intconst` n -> n-1 — `611ea4e54b22`
  - `MAX_SPECIALIST_CONDITIONS = 5`
  - `MAX_SPECIALIST_CONDITIONS = 4`
- **L86** `boolconst` True -> False — `a0ddf2a13221`
  - `model_config = ConfigDict(str_strip_whitespace=True)`
  - `model_config = ConfigDict(str_strip_whitespace=False)`
- **L88** `intconst` n -> n-1 — `4040a45a2ba9`
  - `disease: str = Field(min_length=2, max_length=80)`
  - `disease: str = Field(min_length=1, max_length=80)`
- **L91** `intconst` n -> n-1 — `aa6d6973b29d`
  - `note: str | None = Field(default=None, max_length=500)`
  - `note: str | None = Field(default=None, max_length=499)`
- **L177** `loopjump` continue -> break — `6794455d1038`
  - `continue`
  - `break`
- **L179** `boolop` Or -> And — `92337bd1f7f8`
  - `note = f'(model said: {condition.disease}) ' + (condition.note or '')`
  - `note = f'(model said: {condition.disease}) ' + (condition.note and '')`
- **L180** `intconst` n -> n+1 — `f2a0c8abf375`
  - `conditions.append(condition.model_copy(update={'disease': 'OTHER', 'note': note[:500]}))`
  - `conditions.append(condition.model_copy(update={'disease': 'OTHER', 'note': note[:501]}))`
- **L180** `intconst` n -> n-1 — `8baf49367fa0`
  - `conditions.append(condition.model_copy(update={'disease': 'OTHER', 'note': note[:500]}))`
  - `conditions.append(condition.model_copy(update={'disease': 'OTHER', 'note': note[:499]}))`

### `app/services/simulation_calibration.py` (80 survivors)

- **L48** `binop` BitOr -> BitAnd — `af946b7ba599`
  - `type CalibrationValue = int | float | list[float]`
  - `type CalibrationValue = (int | float) & list[float]`
- **L48** `binop` BitOr -> BitAnd — `330f638fae64`
  - `type CalibrationValue = int | float | list[float]`
  - `type CalibrationValue = int & float | list[float]`
- **L49** `intconst` n -> n+1 — `1423cefd6abc`
  - `_MAX_HISTORY_ROWS = 20000`
  - `_MAX_HISTORY_ROWS = 20001`
- **L49** `intconst` n -> n-1 — `cd183c1fc26a`
  - `_MAX_HISTORY_ROWS = 20000`
  - `_MAX_HISTORY_ROWS = 19999`
- **L55** `binop` Sub -> Add — `d20274f5b953`
  - `months = (reference.year - dob.year) * 12 + reference.month - dob.month`
  - `months = (reference.year - dob.year) * 12 + reference.month + dob.month`
- **L55** `binop` Sub -> Add — `0589ebdc1648`
  - `months = (reference.year - dob.year) * 12 + reference.month - dob.month`
  - `months = (reference.year + dob.year) * 12 + reference.month - dob.month`
- **L55** `intconst` n -> n+1 — `acea520fbe58`
  - `months = (reference.year - dob.year) * 12 + reference.month - dob.month`
  - `months = (reference.year - dob.year) * 13 + reference.month - dob.month`
- **L55** `intconst` n -> n-1 — `312dcc66c4b0`
  - `months = (reference.year - dob.year) * 12 + reference.month - dob.month`
  - `months = (reference.year - dob.year) * 11 + reference.month - dob.month`
- **L62** `intconst` n -> n+1 — `8346e7b0f431`
  - `def _confidence(sample_size: int, *, medium: int=10, high: int=30) -> Literal['low', 'medium', 'high']:
    if sample_size >= high:
        return 'high'
    if sample_size >= medium:
        return 'medium'
    return 'low'`
  - `def _confidence(sample_size: int, *, medium: int=11, high: int=30) -> Literal['low', 'medium', 'high']:
    if sample_size >= high:
        return 'high'
    if sample_size >= medium:
        return 'medium'
    return 'low'`
- **L62** `intconst` n -> n-1 — `a570968515ed`
  - `def _confidence(sample_size: int, *, medium: int=10, high: int=30) -> Literal['low', 'medium', 'high']:
    if sample_size >= high:
        return 'high'
    if sample_size >= medium:
        return 'medium'
    return 'low'`
  - `def _confidence(sample_size: int, *, medium: int=9, high: int=30) -> Literal['low', 'medium', 'high']:
    if sample_size >= high:
        return 'high'
    if sample_size >= medium:
        return 'medium'
    return 'low'`
- **L62** `intconst` n -> n+1 — `eaac30601e3b`
  - `def _confidence(sample_size: int, *, medium: int=10, high: int=30) -> Literal['low', 'medium', 'high']:
    if sample_size >= high:
        return 'high'
    if sample_size >= medium:
        return 'medium'
    return 'low'`
  - `def _confidence(sample_size: int, *, medium: int=10, high: int=31) -> Literal['low', 'medium', 'high']:
    if sample_size >= high:
        return 'high'
    if sample_size >= medium:
        return 'medium'
    return 'low'`
- **L62** `intconst` n -> n-1 — `874f4f244d9f`
  - `def _confidence(sample_size: int, *, medium: int=10, high: int=30) -> Literal['low', 'medium', 'high']:
    if sample_size >= high:
        return 'high'
    if sample_size >= medium:
        return 'medium'
    return 'low'`
  - `def _confidence(sample_size: int, *, medium: int=10, high: int=29) -> Literal['low', 'medium', 'high']:
    if sample_size >= high:
        return 'high'
    if sample_size >= medium:
        return 'medium'
    return 'low'`
- **L160** `intconst` n -> n+1 — `af604db96999`
  - `first_age, last_age = (ages[0], ages[-1])`
  - `first_age, last_age = (ages[1], ages[-1])`
- **L160** `intconst` n -> n+1 — `9a672640c96a`
  - `first_age, last_age = (ages[0], ages[-1])`
  - `first_age, last_age = (ages[0], ages[-2])`
- **L170** `binop` Mult -> Div — `9ae4a7059b2b`
  - `curve.append(preset[age] * _curve_rescale(lookup[last_age], preset[last_age]))`
  - `curve.append(preset[age] / _curve_rescale(lookup[last_age], preset[last_age]))`
- **L172** `compare` Lt -> LtE — `7f7ad19ed247`
  - `lower = max((a for a in ages if a < age))`
  - `lower = max((a for a in ages if a <= age))`
- **L173** `compare` Gt -> GtE — `3d5b4cb7548c`
  - `upper = min((a for a in ages if a > age))`
  - `upper = min((a for a in ages if a >= age))`
- **L174** `binop` Sub -> Add — `b007954536c9`
  - `span = upper - lower`
  - `span = upper + lower`
- **L175** `binop` Add -> Sub — `4646fe07d301`
  - `curve.append(lookup[lower] + (lookup[upper] - lookup[lower]) * (age - lower) / span)`
  - `curve.append(lookup[lower] - (lookup[upper] - lookup[lower]) * (age - lower) / span)`
- **L175** `binop` Mult -> Div — `ff0ea5475c36`
  - `curve.append(lookup[lower] + (lookup[upper] - lookup[lower]) * (age - lower) / span)`
  - `curve.append(lookup[lower] + (lookup[upper] - lookup[lower]) / (age - lower) / span)`
- **L199** `boolconst` True -> False — `51a3ddbe7334`
  - `assumptions = get_preset(breed, system).model_copy(deep=True)`
  - `assumptions = get_preset(breed, system).model_copy(deep=False)`
- **L241** `binop` Sub -> Add — `f52ad139b578`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) + case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
- **L241** `intconst` n -> n+1 — `c61c1aa0bd16`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 13 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
- **L241** `intconst` n -> n-1 — `f5748ed8f607`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 11 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
- **L245** `compare` Gt -> GtE — `da3f665ade48`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) >= reference_date.day, 1), else_=0)`
- **L245** `intconst` n -> n-1 — `cf8fb6da1b9f`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 0), else_=0)`
- **L246** `intconst` n -> n+1 — `5874f1abcc2c`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=0)`
  - `age_months = (reference_date.year - func.extract('year', effective_dob_expr)) * 12 + reference_date.month - func.extract('month', effective_dob_expr) - case((func.extract('day', effective_dob_expr) > reference_date.day, 1), else_=1)`
- **L351** `intconst` n -> n+1 — `f4d643d38e9f`
  - `record(f'herd.{target_key}', count_previous, count_calibrated, current_head, 'Exact ACTIVE-animal cohort count on the reference date', 'animals', medium=1, high=1)`
  - `record(f'herd.{target_key}', count_previous, count_calibrated, current_head, 'Exact ACTIVE-animal cohort count on the reference date', 'animals', medium=2, high=1)`
- **L351** `intconst` n -> n-1 — `8273768d68a7`
  - `record(f'herd.{target_key}', count_previous, count_calibrated, current_head, 'Exact ACTIVE-animal cohort count on the reference date', 'animals', medium=1, high=1)`
  - `record(f'herd.{target_key}', count_previous, count_calibrated, current_head, 'Exact ACTIVE-animal cohort count on the reference date', 'animals', medium=0, high=1)`
- **L392** `boolop` Or -> And — `728c3f78bcbe`
  - `dob = weight_row.date_of_birth or weight_row.estimated_dob`
  - `dob = weight_row.date_of_birth and weight_row.estimated_dob`
- **L556** `compare` LtE -> Lt — `4b8a09f6fd88`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 220]`
  - `valid_gestations = [days for days in gestation_days if 90 < days <= 220]`
- **L556** `intconst` n -> n+1 — `873d7e32e217`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 220]`
  - `valid_gestations = [days for days in gestation_days if 91 <= days <= 220]`
- **L556** `intconst` n -> n-1 — `daa60c97f16a`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 220]`
  - `valid_gestations = [days for days in gestation_days if 89 <= days <= 220]`
- **L556** `intconst` n -> n+1 — `b96450a16ca4`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 220]`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 221]`
- **L556** `intconst` n -> n-1 — `0d82b7be1621`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 220]`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 219]`
- **L559** `intconst` n -> n+1 — `48d423de2bbd`
  - `calibrated_gestation = min(7, max(1, round(median(valid_gestations) / 30.44)))`
  - `calibrated_gestation = min(8, max(1, round(median(valid_gestations) / 30.44)))`
- **L559** `intconst` n -> n-1 — `79c0d9c792a3`
  - `calibrated_gestation = min(7, max(1, round(median(valid_gestations) / 30.44)))`
  - `calibrated_gestation = min(6, max(1, round(median(valid_gestations) / 30.44)))`
- **L559** `intconst` n -> n+1 — `bc8c9a5e4ae8`
  - `calibrated_gestation = min(7, max(1, round(median(valid_gestations) / 30.44)))`
  - `calibrated_gestation = min(7, max(2, round(median(valid_gestations) / 30.44)))`
- **L559** `intconst` n -> n-1 — `e22198616a9c`
  - `calibrated_gestation = min(7, max(1, round(median(valid_gestations) / 30.44)))`
  - `calibrated_gestation = min(7, max(0, round(median(valid_gestations) / 30.44)))`
- **L575** `boolop` And -> Or — `e85be43f33f4`
  - `recorded_birth_weights = [float(kid_row.birth_weight) for kid_row in kidding_rows if kid_row.status != 'STILLBORN' and kid_row.birth_weight is not None and (kid_row.birth_weight > 0.0)]`
  - `recorded_birth_weights = [float(kid_row.birth_weight) for kid_row in kidding_rows if kid_row.status != 'STILLBORN' or kid_row.birth_weight is not None or kid_row.birth_weight > 0.0]`
- **L577** `compare` Gt -> GtE — `8b486425117b`
  - `recorded_birth_weights = [float(kid_row.birth_weight) for kid_row in kidding_rows if kid_row.status != 'STILLBORN' and kid_row.birth_weight is not None and (kid_row.birth_weight > 0.0)]`
  - `recorded_birth_weights = [float(kid_row.birth_weight) for kid_row in kidding_rows if kid_row.status != 'STILLBORN' and kid_row.birth_weight is not None and (kid_row.birth_weight >= 0.0)]`
- **L586** `intconst` n -> n-1 — `b3c7f177800f`
  - `curve[index] = max(curve[index], curve[index - 1])`
  - `curve[index] = max(curve[index], curve[index - 0])`
- **L590** `compare` Eq -> NotEq — `42bbeb4dd114`
  - `curve_evidence = next((item for item in evidence if item.path == 'growth.weight_by_age_months'), None)`
  - `curve_evidence = next((item for item in evidence if item.path != 'growth.weight_by_age_months'), None)`
- **L618** `compare` Eq -> NotEq — `e3d03a5c6b6c`
  - `stillborn = sum((kid_row.status == 'STILLBORN' for kid_row in kidding_rows))`
  - `stillborn = sum((kid_row.status != 'STILLBORN' for kid_row in kidding_rows))`
- **L620** `compare` Eq -> NotEq — `74d2ed5df2ce`
  - `female_alive = sum((kid_row.sex == 'F' for kid_row in alive_rows))`
  - `female_alive = sum((kid_row.sex != 'F' for kid_row in alive_rows))`
- **L625** `binop` Div -> Mult — `778d6844d1fb`
  - `observed_stillbirth = stillborn / kid_count`
  - `observed_stillbirth = stillborn * kid_count`
- **L655** `intconst` n -> n+1 — `94225249a03b`
  - `weaning_cutoff = add_months(reference_date, -3)`
  - `weaning_cutoff = add_months(reference_date, -4)`
- **L655** `intconst` n -> n-1 — `a8ed9bd3d58a`
  - `weaning_cutoff = add_months(reference_date, -3)`
  - `weaning_cutoff = add_months(reference_date, -2)`
- **L656** `compare` LtE -> Lt — `e80a5711d534`
  - `weaned_rows = [row for row in alive_rows if row.date <= weaning_cutoff]`
  - `weaned_rows = [row for row in alive_rows if row.date < weaning_cutoff]`
- **L661** `compare` LtE -> Lt — `779d24f8c62a`
  - `died = sum((kid_row.status == 'DIED' and kid_row.mortality_reported_at is not None and (kid_row.mortality_reported_at <= add_months(kid_row.date, 3)) for kid_row in weaned_rows))`
  - `died = sum((kid_row.status == 'DIED' and kid_row.mortality_reported_at is not None and (kid_row.mortality_reported_at < add_months(kid_row.date, 3)) for kid_row in weaned_rows))`
- **L661** `intconst` n -> n-1 — `ad5fa11becba`
  - `died = sum((kid_row.status == 'DIED' and kid_row.mortality_reported_at is not None and (kid_row.mortality_reported_at <= add_months(kid_row.date, 3)) for kid_row in weaned_rows))`
  - `died = sum((kid_row.status == 'DIED' and kid_row.mortality_reported_at is not None and (kid_row.mortality_reported_at <= add_months(kid_row.date, 2)) for kid_row in weaned_rows))`
- **L698** `boolop` Or -> And — `a6c6acaa9e14`
  - `dob = mortality_row.date_of_birth or mortality_row.estimated_dob`
  - `dob = mortality_row.date_of_birth and mortality_row.estimated_dob`
- **L702** `compare` LtE -> Lt — `a243b0a95e1a`
  - `died_in_window = mortality_row.status == AnimalStatus.DEAD.value and mortality_row.status_date is not None and (period_start <= mortality_row.status_date <= reference_date)`
  - `died_in_window = mortality_row.status == AnimalStatus.DEAD.value and mortality_row.status_date is not None and (period_start < mortality_row.status_date <= reference_date)`
- **L708** `boolop` And -> Or — `d8e436855d91`
  - `left_on = mortality_row.status_date if mortality_row.status != AnimalStatus.ACTIVE.value and mortality_row.status_date is not None else reference_date`
  - `left_on = mortality_row.status_date if mortality_row.status != AnimalStatus.ACTIVE.value or mortality_row.status_date is not None else reference_date`
- **L716** `boolop` Or -> And — `2e2f8da80dda`
  - `exposure_start = max(period_start, mortality_row.purchase_date or period_start)`
  - `exposure_start = max(period_start, mortality_row.purchase_date and period_start)`
- **L723** `loopjump` continue -> break — `63a62dfd46b6`
  - `continue`
  - `break`
- **L778** `boolop` Or -> And — `350f04e4380e`
  - `purchase_age = _age_months(pricing_row.date_of_birth or pricing_row.estimated_dob, pricing_row.purchase_date)`
  - `purchase_age = _age_months(pricing_row.date_of_birth and pricing_row.estimated_dob, pricing_row.purchase_date)`
- **L782** `ifexp` swap branches — `4a04b7eb21f9`
  - `adult_age = assumptions.reproduction.age_at_first_breeding_months if pricing_row.sex == 'F' else 12`
  - `adult_age = 12 if pricing_row.sex == 'F' else assumptions.reproduction.age_at_first_breeding_months`
- **L783** `compare` Eq -> NotEq — `c0055b3288ed`
  - `adult_age = assumptions.reproduction.age_at_first_breeding_months if pricing_row.sex == 'F' else 12`
  - `adult_age = assumptions.reproduction.age_at_first_breeding_months if pricing_row.sex != 'F' else 12`
- **L784** `intconst` n -> n+1 — `e778aec33afc`
  - `adult_age = assumptions.reproduction.age_at_first_breeding_months if pricing_row.sex == 'F' else 12`
  - `adult_age = assumptions.reproduction.age_at_first_breeding_months if pricing_row.sex == 'F' else 13`
- **L784** `intconst` n -> n-1 — `e67c9ca41cd4`
  - `adult_age = assumptions.reproduction.age_at_first_breeding_months if pricing_row.sex == 'F' else 12`
  - `adult_age = assumptions.reproduction.age_at_first_breeding_months if pricing_row.sex == 'F' else 11`
- **L844** `boolop` Or -> And — `18f1c582a6da`
  - `festival_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month > 0`
  - `festival_active = bool(assumptions.sales.festival_sale_months) and assumptions.sales.eid_month > 0`
- **L847** `binop` Div -> Mult — `8c42e0c38005`
  - `festival_deflator = 1.0 / (1.0 + assumptions.sales.eid_price_uplift)`
  - `festival_deflator = 1.0 * (1.0 + assumptions.sales.eid_price_uplift)`
- **L847** `binop` Add -> Sub — `9953fa21a948`
  - `festival_deflator = 1.0 / (1.0 + assumptions.sales.eid_price_uplift)`
  - `festival_deflator = 1.0 / (1.0 - assumptions.sales.eid_price_uplift)`
- **L988** `intconst` n -> n+1 — `8f14d9de8490`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((2 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
- **L988** `intconst` n -> n-1 — `79e4e2a73e6e`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((0 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
- **L990** `boolop` And -> Or — `6ab4a8756b55`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category or transaction_row.feed_quantity_kg is not None or transaction_row.feed_unit_price_per_kg is not None))`
- **L990** `compare` Eq -> NotEq — `273cb9130ec7`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category != feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
- **L991** `compare` IsNot -> Is — `659467612979`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is None and (transaction_row.feed_unit_price_per_kg is not None)))`
- **L992** `compare` IsNot -> Is — `dc1fd4ca61b2`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is None)))`
- **L1016** `intconst` n -> n-1 — `7eebcc558cff`
  - `observed_months = max(1, ceil(_months_between(true_first_expense, reference_date)))`
  - `observed_months = max(0, ceil(_months_between(true_first_expense, reference_date)))`
- **L1051** `intconst` n -> n+1 — `152f3b5de3a1`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((2 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
- **L1051** `intconst` n -> n-1 — `310607ea54bb`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((0 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
- **L1051** `compare` Eq -> NotEq — `afabd52d2170`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category != 'LABOUR'))`
- **L1069** `intconst` n -> n+1 — `f5f4cb900789`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((2 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
- **L1069** `intconst` n -> n-1 — `7be0e1b67d1a`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((0 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
- **L1069** `compare` Eq -> NotEq — `61dd43f01fbe`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category != 'LABOUR'))`
- **L1084** `binop` Add -> Sub — `8fb5ad51fa59`
  - `vet_total = category_expense['VET'] + category_expense['MEDICINE']`
  - `vet_total = category_expense['VET'] - category_expense['MEDICINE']`
- **L1140** `intconst` n -> n+1 — `a16d303e372d`
  - `covered_groups = {item.path.split('.', maxsplit=1)[0] for item in evidence}`
  - `covered_groups = {item.path.split('.', maxsplit=2)[0] for item in evidence}`
- **L1149** `compare` Eq -> NotEq — `acf40c6767a7`
  - `low_confidence = sum((item.confidence == 'low' for item in evidence))`
  - `low_confidence = sum((item.confidence != 'low' for item in evidence))`

### `app/services/tasks.py` (9 survivors)

- **L207** `compare` IsNot -> Is — `83b7b52fd9e9`
  - `animal_ids = [kid.animal_id for kid in kids if kid.animal_id is not None]`
  - `animal_ids = [kid.animal_id for kid in kids if kid.animal_id is None]`
- **L531** `intconst` n -> n+1 — `42884e4f32e3`
  - `candidates.insert(0, weaning_doe)`
  - `candidates.insert(1, weaning_doe)`
- **L556** `ifexp` swap branches — `75fdebcff494`
  - `move_animal(db, animal, Bucket.FOUNDATION.value, '45-day quarantine complete', created_by_id=user.id if user else None, context='quarantine_release', reference_date=resolved_movement_date)`
  - `move_animal(db, animal, Bucket.FOUNDATION.value, '45-day quarantine complete', created_by_id=None if user else user.id, context='quarantine_release', reference_date=resolved_movement_date)`
- **L574** `ifexp` swap branches — `a2578300b76d`
  - `move_animal(db, postpartum_doe, Bucket.RESTING.value, 'Postpartum recovery complete; no surviving kids', created_by_id=user.id if user else None, context='postpartum', reference_date=resolved_movement_date)`
  - `move_animal(db, postpartum_doe, Bucket.RESTING.value, 'Postpartum recovery complete; no surviving kids', created_by_id=None if user else user.id, context='postpartum', reference_date=resolved_movement_date)`
- **L596** `ifexp` swap branches — `d8b140347f70`
  - `move_animal(db, day100_doe, Bucket.PREGNANCY_LATE.value, 'Gestation day 100 (ration step-up)', created_by_id=user.id if user else None, context='manual', reference_date=resolved_movement_date)`
  - `move_animal(db, day100_doe, Bucket.PREGNANCY_LATE.value, 'Gestation day 100 (ration step-up)', created_by_id=None if user else user.id, context='manual', reference_date=resolved_movement_date)`
- **L617** `ifexp` swap branches — `4fda91db7a7a`
  - `move_animal(db, linked_animal, Bucket.DELIVERY.value, '~2 weeks before due date', created_by_id=user.id if user else None, context='delivery', reference_date=resolved_movement_date)`
  - `move_animal(db, linked_animal, Bucket.DELIVERY.value, '~2 weeks before due date', created_by_id=None if user else user.id, context='delivery', reference_date=resolved_movement_date)`
- **L758** `ifexp` swap branches — `a4829e228665`
  - `business_today = today(farm.timezone) if farm is not None else today()`
  - `business_today = today() if farm is not None else today(farm.timezone)`
- **L758** `compare` IsNot -> Is — `da967801b519`
  - `business_today = today(farm.timezone) if farm is not None else today()`
  - `business_today = today(farm.timezone) if farm is None else today()`
- **L963** `compare` Eq -> NotEq — `51f551c46f24`
  - `retained_assignee_has_viewer_role = select(FarmMembership.id).where(FarmMembership.farm_id == Task.farm_id, FarmMembership.user_id == Task.assigned_user_id, FarmMembership.role_id == membership.role_id).exists()`
  - `retained_assignee_has_viewer_role = select(FarmMembership.id).where(FarmMembership.farm_id == Task.farm_id, FarmMembership.user_id != Task.assigned_user_id, FarmMembership.role_id == membership.role_id).exists()`

### `app/simulation/assumptions.py` (14 survivors)

- **L232** `intconst` n -> n-1 — `04261eb63517`
  - `age_at_first_breeding_months: int = Field(default=12, ge=6, le=30)`
  - `age_at_first_breeding_months: int = Field(default=12, ge=5, le=30)`
- **L232** `intconst` n -> n+1 — `de4acbc82cad`
  - `age_at_first_breeding_months: int = Field(default=12, ge=6, le=30)`
  - `age_at_first_breeding_months: int = Field(default=12, ge=6, le=31)`
- **L293** `intconst` n -> n+1 — `65feb019779f`
  - `max_doe_age_months: int = Field(default=72, ge=36, le=180)`
  - `max_doe_age_months: int = Field(default=72, ge=36, le=181)`
- **L298** `intconst` n -> n-1 — `c90150673b9f`
  - `buck_doe_ratio: int = Field(default=20, ge=1, le=100)`
  - `buck_doe_ratio: int = Field(default=20, ge=1, le=99)`
- **L401** `intconst` n -> n-1 — `e74711a32078`
  - `sale_age_months: int = Field(default=9, ge=6, le=24)`
  - `sale_age_months: int = Field(default=9, ge=5, le=24)`
- **L401** `intconst` n -> n+1 — `4a1e191c9dcf`
  - `sale_age_months: int = Field(default=9, ge=6, le=24)`
  - `sale_age_months: int = Field(default=9, ge=6, le=25)`
- **L496** `intconst` n -> n+1 — `28e8863147fd`
  - `eid_month: int = Field(default=0, ge=0, le=12)`
  - `eid_month: int = Field(default=1, ge=0, le=12)`
- **L496** `intconst` n -> n+1 — `5a60b43270d2`
  - `eid_month: int = Field(default=0, ge=0, le=12)`
  - `eid_month: int = Field(default=0, ge=0, le=13)`
- **L509** `intconst` n -> n-1 — `6825da8ca0fe`
  - `festival_sale_months: list[int] | None = Field(default=None, max_length=40)`
  - `festival_sale_months: list[int] | None = Field(default=None, max_length=39)`
- **L516** `intconst` n -> n-1 — `3e1023f9de3a`
  - `festival_hold_months: int = Field(default=2, ge=0, le=12)`
  - `festival_hold_months: int = Field(default=2, ge=0, le=11)`
- **L834** `intconst` n -> n-1 — `03bc76b60755`
  - `seed: int = Field(default=42, ge=0, le=2 ** 31 - 1)`
  - `seed: int = Field(default=42, ge=0, le=2 ** 30 - 1)`
- **L877** `intconst` n -> n+1 — `f73f67f184fe`
  - `market_crash_duration_months: int = Field(default=3, ge=1, le=24)`
  - `market_crash_duration_months: int = Field(default=4, ge=1, le=24)`
- **L877** `intconst` n -> n+1 — `392e256ad419`
  - `market_crash_duration_months: int = Field(default=3, ge=1, le=24)`
  - `market_crash_duration_months: int = Field(default=3, ge=2, le=24)`
- **L896** `intconst` n -> n+1 — `9231903bd568`
  - `sale_age_radius_months: int = Field(default=2, ge=0, le=9)`
  - `sale_age_radius_months: int = Field(default=2, ge=0, le=10)`

### `app/simulation/backward_planner.py` (28 survivors)

- **L221** `binop` FloorDiv -> Mult — `868aa6451185`
  - `self.typical_age = (self.entry_age + self.max_age) // 2`
  - `self.typical_age = (self.entry_age + self.max_age) * 2`
- **L221** `intconst` n -> n-1 — `6b2bb24c2724`
  - `self.typical_age = (self.entry_age + self.max_age) // 2`
  - `self.typical_age = (self.entry_age + self.max_age) // 1`
- **L268** `binop` Mult -> Div — `e3c72a7d111d`
  - `per_birth_of_sex = r.litter_size * (1.0 - r.stillbirth_rate) * sex_share`
  - `per_birth_of_sex = r.litter_size / (1.0 - r.stillbirth_rate) * sex_share`
- **L268** `binop` Sub -> Add — `712b15a188c5`
  - `per_birth_of_sex = r.litter_size * (1.0 - r.stillbirth_rate) * sex_share`
  - `per_birth_of_sex = r.litter_size * (1.0 + r.stillbirth_rate) * sex_share`
- **L280** `binop` Div -> Mult — `5bef16523b94`
  - `kids_of_sex_needed = math.ceil(target.count / survival)`
  - `kids_of_sex_needed = math.ceil(target.count * survival)`
- **L318** `binop` Sub -> Add — `4c466f2c30ba`
  - `young_loss_pct = round((1.0 - survival) * 100.0)`
  - `young_loss_pct = round((1.0 + survival) * 100.0)`
- **L324** `binop` Mult -> Div — `5f4a4a7272d4`
  - `explanation = f'Selling {target.count:g} {nouns.event_label(target.animal_class)} at ~{window.typical_age} months needs ~{kids_of_sex_needed} alive at sale; with {young_loss_pct}% born-to-sale mortality that is ~{total_kids_needed} {nouns.young_plural} born around {month_label(start, birth_month)} (litter size {r.litter_size:g}, {round(sex_share * 100)}% of the sex you sell), from ~{does_kidded} {nouns.female}(s) {nouns.parturition} and ~{does_bred} bred about {r.gestation_months} months earlier. Your herd must hold that many breedable {nouns.female_plural} then — buy early enough to settle, or retain more young {nouns.female_plural}.'`
  - `explanation = f'Selling {target.count:g} {nouns.event_label(target.animal_class)} at ~{window.typical_age} months needs ~{kids_of_sex_needed} alive at sale; with {young_loss_pct}% born-to-sale mortality that is ~{total_kids_needed} {nouns.young_plural} born around {month_label(start, birth_month)} (litter size {r.litter_size:g}, {round(sex_share / 100)}% of the sex you sell), from ~{does_kidded} {nouns.female}(s) {nouns.parturition} and ~{does_bred} bred about {r.gestation_months} months earlier. Your herd must hold that many breedable {nouns.female_plural} then — buy early enough to settle, or retain more young {nouns.female_plural}.'`
- **L324** `intconst` n -> n+1 — `f209c7c6aac0`
  - `explanation = f'Selling {target.count:g} {nouns.event_label(target.animal_class)} at ~{window.typical_age} months needs ~{kids_of_sex_needed} alive at sale; with {young_loss_pct}% born-to-sale mortality that is ~{total_kids_needed} {nouns.young_plural} born around {month_label(start, birth_month)} (litter size {r.litter_size:g}, {round(sex_share * 100)}% of the sex you sell), from ~{does_kidded} {nouns.female}(s) {nouns.parturition} and ~{does_bred} bred about {r.gestation_months} months earlier. Your herd must hold that many breedable {nouns.female_plural} then — buy early enough to settle, or retain more young {nouns.female_plural}.'`
  - `explanation = f'Selling {target.count:g} {nouns.event_label(target.animal_class)} at ~{window.typical_age} months needs ~{kids_of_sex_needed} alive at sale; with {young_loss_pct}% born-to-sale mortality that is ~{total_kids_needed} {nouns.young_plural} born around {month_label(start, birth_month)} (litter size {r.litter_size:g}, {round(sex_share * 101)}% of the sex you sell), from ~{does_kidded} {nouns.female}(s) {nouns.parturition} and ~{does_bred} bred about {r.gestation_months} months earlier. Your herd must hold that many breedable {nouns.female_plural} then — buy early enough to settle, or retain more young {nouns.female_plural}.'`
- **L324** `intconst` n -> n-1 — `4d63e99a3323`
  - `explanation = f'Selling {target.count:g} {nouns.event_label(target.animal_class)} at ~{window.typical_age} months needs ~{kids_of_sex_needed} alive at sale; with {young_loss_pct}% born-to-sale mortality that is ~{total_kids_needed} {nouns.young_plural} born around {month_label(start, birth_month)} (litter size {r.litter_size:g}, {round(sex_share * 100)}% of the sex you sell), from ~{does_kidded} {nouns.female}(s) {nouns.parturition} and ~{does_bred} bred about {r.gestation_months} months earlier. Your herd must hold that many breedable {nouns.female_plural} then — buy early enough to settle, or retain more young {nouns.female_plural}.'`
  - `explanation = f'Selling {target.count:g} {nouns.event_label(target.animal_class)} at ~{window.typical_age} months needs ~{kids_of_sex_needed} alive at sale; with {young_loss_pct}% born-to-sale mortality that is ~{total_kids_needed} {nouns.young_plural} born around {month_label(start, birth_month)} (litter size {r.litter_size:g}, {round(sex_share * 99)}% of the sex you sell), from ~{does_kidded} {nouns.female}(s) {nouns.parturition} and ~{does_bred} bred about {r.gestation_months} months earlier. Your herd must hold that many breedable {nouns.female_plural} then — buy early enough to settle, or retain more young {nouns.female_plural}.'`
- **L417** `binop` Add -> Sub — `bce4328d980f`
  - `class_leads = [_class_max_age(animal_class, sale_age, afb) + r.gestation_months + herd.purchased_doe_settling_months + 1 for animal_class in target_classes or ['male_grower']]`
  - `class_leads = [_class_max_age(animal_class, sale_age, afb) + r.gestation_months + herd.purchased_doe_settling_months - 1 for animal_class in target_classes or ['male_grower']]`
- **L417** `binop` Add -> Sub — `e66c47ed2918`
  - `class_leads = [_class_max_age(animal_class, sale_age, afb) + r.gestation_months + herd.purchased_doe_settling_months + 1 for animal_class in target_classes or ['male_grower']]`
  - `class_leads = [_class_max_age(animal_class, sale_age, afb) + r.gestation_months - herd.purchased_doe_settling_months + 1 for animal_class in target_classes or ['male_grower']]`
- **L417** `binop` Add -> Sub — `41e5510ef929`
  - `class_leads = [_class_max_age(animal_class, sale_age, afb) + r.gestation_months + herd.purchased_doe_settling_months + 1 for animal_class in target_classes or ['male_grower']]`
  - `class_leads = [_class_max_age(animal_class, sale_age, afb) - r.gestation_months + herd.purchased_doe_settling_months + 1 for animal_class in target_classes or ['male_grower']]`
- **L420** `intconst` n -> n+1 — `ae1c871b0bf6`
  - `class_leads = [_class_max_age(animal_class, sale_age, afb) + r.gestation_months + herd.purchased_doe_settling_months + 1 for animal_class in target_classes or ['male_grower']]`
  - `class_leads = [_class_max_age(animal_class, sale_age, afb) + r.gestation_months + herd.purchased_doe_settling_months + 2 for animal_class in target_classes or ['male_grower']]`
- **L424** `binop` Sub -> Add — `23363732f679`
  - `grow_months = lead - r.gestation_months - herd.purchased_doe_settling_months - 1`
  - `grow_months = lead - r.gestation_months + herd.purchased_doe_settling_months - 1`
- **L424** `intconst` n -> n+1 — `f3c4385fe2e0`
  - `grow_months = lead - r.gestation_months - herd.purchased_doe_settling_months - 1`
  - `grow_months = lead - r.gestation_months - herd.purchased_doe_settling_months - 2`
- **L424** `intconst` n -> n-1 — `6497ec0d9022`
  - `grow_months = lead - r.gestation_months - herd.purchased_doe_settling_months - 1`
  - `grow_months = lead - r.gestation_months - herd.purchased_doe_settling_months - 0`
- **L427** `binop` Add -> Sub — `14b17e2ef2af`
  - `by_month[event.month] = by_month.get(event.month, 0.0) + event.count`
  - `by_month[event.month] = by_month.get(event.month, 0.0) - event.count`
- **L496** `boolop` Or -> And — `247f0869a3ff`
  - `core = _run_core(_plan_assumptions(variant, sale_targets, purchases or None))`
  - `core = _run_core(_plan_assumptions(variant, sale_targets, purchases and None))`
- **L519** `ifexp` swap branches — `4833ee8248f5`
  - `actions.append(PlannerAction(month=offset, year_month=label, kind='sell', headline=f'Sell {target.count:g} {nouns.event_label(target.animal_class)}', detail=f'Planned revenue ≈ ₹{fill.revenue:,.0f}. ' + ('The closed plan fills this target.' if achievable else f'Short by {fill.shortfall:.1f} head even after purchases — reduce the target or move it later.')))`
  - `actions.append(PlannerAction(month=offset, year_month=label, kind='sell', headline=f'Sell {target.count:g} {nouns.event_label(target.animal_class)}', detail=f'Planned revenue ≈ ₹{fill.revenue:,.0f}. ' + (f'Short by {fill.shortfall:.1f} head even after purchases — reduce the target or move it later.' if achievable else 'The closed plan fills this target.')))`
- **L536** `compare` Lt -> LtE — `e639f82d7018`
  - `missed_deadline = bred_month < 1`
  - `missed_deadline = bred_month <= 1`
- **L536** `intconst` n -> n+1 — `dc65962aa70c`
  - `missed_deadline = bred_month < 1`
  - `missed_deadline = bred_month < 2`
- **L543** `intconst` n -> n+1 — `21685bff7f0b`
  - `actions.append(PlannerAction(month=bred_month, year_month=month_label(start, bred_month), kind='breed', headline=f'Breed ~{chain.steps[0].quantity} {nouns.female_plural} ({nouns.event_label(target.animal_class)} for {label})', detail='This breeding window is already behind you — the target cannot be met now; move the sale later or source young stock directly.' if missed_deadline else f'Serve every open, settled doe this month; conception ≈ {variant.reproduction.conception_rate:.0%} per service.'))`
  - `actions.append(PlannerAction(month=bred_month, year_month=month_label(start, bred_month), kind='breed', headline=f'Breed ~{chain.steps[1].quantity} {nouns.female_plural} ({nouns.event_label(target.animal_class)} for {label})', detail='This breeding window is already behind you — the target cannot be met now; move the sale later or source young stock directly.' if missed_deadline else f'Serve every open, settled doe this month; conception ≈ {variant.reproduction.conception_rate:.0%} per service.'))`
- **L547** `ifexp` swap branches — `26160efbe96e`
  - `actions.append(PlannerAction(month=bred_month, year_month=month_label(start, bred_month), kind='breed', headline=f'Breed ~{chain.steps[0].quantity} {nouns.female_plural} ({nouns.event_label(target.animal_class)} for {label})', detail='This breeding window is already behind you — the target cannot be met now; move the sale later or source young stock directly.' if missed_deadline else f'Serve every open, settled doe this month; conception ≈ {variant.reproduction.conception_rate:.0%} per service.'))`
  - `actions.append(PlannerAction(month=bred_month, year_month=month_label(start, bred_month), kind='breed', headline=f'Breed ~{chain.steps[0].quantity} {nouns.female_plural} ({nouns.event_label(target.animal_class)} for {label})', detail=f'Serve every open, settled doe this month; conception ≈ {variant.reproduction.conception_rate:.0%} per service.' if missed_deadline else 'This breeding window is already behind you — the target cannot be met now; move the sale later or source young stock directly.'))`
- **L562** `intconst` n -> n+1 — `c80e4660efde`
  - `actions.append(PlannerAction(month=birth_month, year_month=month_label(start, birth_month), kind='expect_births', headline=f'Expect ~{chain.steps[2].quantity} {nouns.young_plural} born (target {label})', detail=f'{nouns.parturition.capitalize()}-season care decides the mortality the plan assumes (young-stock loss ≈ {variant.mortality.kid_pre_weaning:.0%} before weaning).'))`
  - `actions.append(PlannerAction(month=birth_month, year_month=month_label(start, birth_month), kind='expect_births', headline=f'Expect ~{chain.steps[3].quantity} {nouns.young_plural} born (target {label})', detail=f'{nouns.parturition.capitalize()}-season care decides the mortality the plan assumes (young-stock loss ≈ {variant.mortality.kid_pre_weaning:.0%} before weaning).'))`
- **L562** `intconst` n -> n-1 — `ac6706188f7a`
  - `actions.append(PlannerAction(month=birth_month, year_month=month_label(start, birth_month), kind='expect_births', headline=f'Expect ~{chain.steps[2].quantity} {nouns.young_plural} born (target {label})', detail=f'{nouns.parturition.capitalize()}-season care decides the mortality the plan assumes (young-stock loss ≈ {variant.mortality.kid_pre_weaning:.0%} before weaning).'))`
  - `actions.append(PlannerAction(month=birth_month, year_month=month_label(start, birth_month), kind='expect_births', headline=f'Expect ~{chain.steps[1].quantity} {nouns.young_plural} born (target {label})', detail=f'{nouns.parturition.capitalize()}-season care decides the mortality the plan assumes (young-stock loss ≈ {variant.mortality.kid_pre_weaning:.0%} before weaning).'))`
- **L578** `intconst` n -> n+1 — `3c7559d05767`
  - `actions.append(PlannerAction(month=1, year_month=start, kind='retain', headline=f'Retain more female {nouns.young_plural}', detail=f'Retention is {variant.herd.female_retention_fraction:.0%} today; every retained daughter replaces a purchased doe later. Raise retention (and the breeding-doe cap if it binds) to cut the purchase bill.'))`
  - `actions.append(PlannerAction(month=2, year_month=start, kind='retain', headline=f'Retain more female {nouns.young_plural}', detail=f'Retention is {variant.herd.female_retention_fraction:.0%} today; every retained daughter replaces a purchased doe later. Raise retention (and the breeding-doe cap if it binds) to cut the purchase bill.'))`
- **L578** `intconst` n -> n-1 — `478b36e39451`
  - `actions.append(PlannerAction(month=1, year_month=start, kind='retain', headline=f'Retain more female {nouns.young_plural}', detail=f'Retention is {variant.herd.female_retention_fraction:.0%} today; every retained daughter replaces a purchased doe later. Raise retention (and the breeding-doe cap if it binds) to cut the purchase bill.'))`
  - `actions.append(PlannerAction(month=0, year_month=start, kind='retain', headline=f'Retain more female {nouns.young_plural}', detail=f'Retention is {variant.herd.female_retention_fraction:.0%} today; every retained daughter replaces a purchased doe later. Raise retention (and the breeding-doe cap if it binds) to cut the purchase bill.'))`
- **L604** `intconst` n -> n+1 — `fad18fdb9c2c`
  - `notes.insert(0, f'Plan runs {start} → {month_label(start, last_month)} ({last_month} months). Month 1 is {start}.')`
  - `notes.insert(1, f'Plan runs {start} → {month_label(start, last_month)} ({last_month} months). Month 1 is {start}.')`

### `app/simulation/daily_ops.py` (108 survivors)

- **L185** `boolconst` True -> False — `4b25aeab9df2`
  - `model_config = ConfigDict(extra='forbid', strict=True)`
  - `model_config = ConfigDict(extra='forbid', strict=False)`
- **L187** `intconst` n -> n-1 — `865e1688d282`
  - `tag: str = Field(min_length=1, max_length=50)`
  - `tag: str = Field(min_length=0, max_length=50)`
- **L187** `intconst` n -> n+1 — `46d5a6f93105`
  - `tag: str = Field(min_length=1, max_length=50)`
  - `tag: str = Field(min_length=1, max_length=51)`
- **L187** `intconst` n -> n-1 — `25b6aea5d935`
  - `tag: str = Field(min_length=1, max_length=50)`
  - `tag: str = Field(min_length=1, max_length=49)`
- **L190** `intconst` n -> n+1 — `b7c6c8e6f5b1`
  - `age_months: int = Field(ge=0, le=_MAX_AGE_MONTHS_START)`
  - `age_months: int = Field(ge=1, le=_MAX_AGE_MONTHS_START)`
- **L193** `intconst` n -> n+1 — `c9f8ce7b8240`
  - `days_in_bucket: int = Field(default=0, ge=0, le=3650)`
  - `days_in_bucket: int = Field(default=1, ge=0, le=3650)`
- **L193** `intconst` n -> n+1 — `5585443dd4ea`
  - `days_in_bucket: int = Field(default=0, ge=0, le=3650)`
  - `days_in_bucket: int = Field(default=0, ge=0, le=3651)`
- **L193** `intconst` n -> n-1 — `cb9a7c42f129`
  - `days_in_bucket: int = Field(default=0, ge=0, le=3650)`
  - `days_in_bucket: int = Field(default=0, ge=0, le=3649)`
- **L197** `intconst` n -> n+1 — `5d77f0c27822`
  - `bred_days_ago: int | None = Field(default=None, ge=0, le=_PROFILE.gestation_days)`
  - `bred_days_ago: int | None = Field(default=None, ge=1, le=_PROFILE.gestation_days)`
- **L226** `boolconst` True -> False — `a4a893752cd2`
  - `model_config = ConfigDict(extra='forbid', strict=True)`
  - `model_config = ConfigDict(extra='forbid', strict=False)`
- **L236** `intconst` n -> n+1 — `26e733376cbd`
  - `failed_services_before_cull: int = Field(default=_PROFILE.failed_services_before_cull, ge=1, le=6)`
  - `failed_services_before_cull: int = Field(default=_PROFILE.failed_services_before_cull, ge=2, le=6)`
- **L236** `intconst` n -> n-1 — `96a61a86f611`
  - `failed_services_before_cull: int = Field(default=_PROFILE.failed_services_before_cull, ge=1, le=6)`
  - `failed_services_before_cull: int = Field(default=_PROFILE.failed_services_before_cull, ge=0, le=6)`
- **L236** `intconst` n -> n+1 — `b7eb87e673d6`
  - `failed_services_before_cull: int = Field(default=_PROFILE.failed_services_before_cull, ge=1, le=6)`
  - `failed_services_before_cull: int = Field(default=_PROFILE.failed_services_before_cull, ge=1, le=7)`
- **L236** `intconst` n -> n-1 — `30e1f3c77c01`
  - `failed_services_before_cull: int = Field(default=_PROFILE.failed_services_before_cull, ge=1, le=6)`
  - `failed_services_before_cull: int = Field(default=_PROFILE.failed_services_before_cull, ge=1, le=5)`
- **L238** `intconst` n -> n+1 — `233770b785ea`
  - `max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=12, le=240)`
  - `max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=13, le=240)`
- **L238** `intconst` n -> n-1 — `7b5bee9f8fce`
  - `max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=12, le=240)`
  - `max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=11, le=240)`
- **L238** `intconst` n -> n+1 — `5e32411dabe5`
  - `max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=12, le=240)`
  - `max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=12, le=241)`
- **L238** `intconst` n -> n-1 — `294af2d59031`
  - `max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=12, le=240)`
  - `max_doe_age_months: int = Field(default=_MAX_DOE_AGE_MONTHS, ge=12, le=239)`
- **L239** `intconst` n -> n-1 — `b2427c10f83c`
  - `male_sale_age_months: int = Field(default=MEAT_SALE_AGE_MONTHS[0], ge=1, le=36)`
  - `male_sale_age_months: int = Field(default=MEAT_SALE_AGE_MONTHS[0], ge=0, le=36)`
- **L239** `intconst` n -> n+1 — `352fd3f1d310`
  - `male_sale_age_months: int = Field(default=MEAT_SALE_AGE_MONTHS[0], ge=1, le=36)`
  - `male_sale_age_months: int = Field(default=MEAT_SALE_AGE_MONTHS[0], ge=1, le=37)`
- **L239** `intconst` n -> n-1 — `bbf4c53ab0b1`
  - `male_sale_age_months: int = Field(default=MEAT_SALE_AGE_MONTHS[0], ge=1, le=36)`
  - `male_sale_age_months: int = Field(default=MEAT_SALE_AGE_MONTHS[0], ge=1, le=35)`
- **L240** `intconst` n -> n+1 — `6abcdca6c44c`
  - `buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=1, le=100)`
  - `buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=2, le=100)`
- **L240** `intconst` n -> n-1 — `64ef04135962`
  - `buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=1, le=100)`
  - `buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=0, le=100)`
- **L240** `intconst` n -> n+1 — `22ef9d611930`
  - `buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=1, le=100)`
  - `buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=1, le=101)`
- **L240** `intconst` n -> n-1 — `7df1a52dee41`
  - `buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=1, le=100)`
  - `buck_doe_ratio: int = Field(default=BUCK_DOE_RATIO, ge=1, le=99)`
- **L244** `boolconst` True -> False — `a9e0e35e313b`
  - `model_config = ConfigDict(extra='forbid', strict=True)`
  - `model_config = ConfigDict(extra='forbid', strict=False)`
- **L247** `intconst` n -> n+1 — `6cc83e4f9656`
  - `horizon_days: int = Field(default=90, ge=MIN_HORIZON_DAYS, le=MAX_HORIZON_DAYS)`
  - `horizon_days: int = Field(default=91, ge=MIN_HORIZON_DAYS, le=MAX_HORIZON_DAYS)`
- **L247** `intconst` n -> n-1 — `4dfd74a0fa14`
  - `horizon_days: int = Field(default=90, ge=MIN_HORIZON_DAYS, le=MAX_HORIZON_DAYS)`
  - `horizon_days: int = Field(default=89, ge=MIN_HORIZON_DAYS, le=MAX_HORIZON_DAYS)`
- **L249** `intconst` n -> n+1 — `adf78e247174`
  - `seed: int = Field(default=2026, ge=-2 ** 62, le=2 ** 62)`
  - `seed: int = Field(default=2027, ge=-2 ** 62, le=2 ** 62)`
- **L249** `intconst` n -> n-1 — `1148936728b2`
  - `seed: int = Field(default=2026, ge=-2 ** 62, le=2 ** 62)`
  - `seed: int = Field(default=2025, ge=-2 ** 62, le=2 ** 62)`
- **L280** `binop` Sub -> Add — `7afccdc90c12`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 1), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late + 1), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
- **L280** `intconst` n -> n+1 — `88a3d9243a0b`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 1), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 2), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
- **L280** `intconst` n -> n-1 — `59345d3176f3`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 1), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 0), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
- **L281** `binop` Sub -> Add — `4452d62449fb`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 1), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 1), Bucket.PREGNANCY_LATE.value: (late, prepartum + 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
- **L282** `binop` Sub -> Add — `65d1b0edb65a`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 1), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation - 1)}[a.bucket]`
  - `low, high = {Bucket.PREGNANCY_EARLY.value: (scan, late - 1), Bucket.PREGNANCY_LATE.value: (late, prepartum - 1), Bucket.DELIVERY.value: (prepartum, gestation + 1)}[a.bucket]`
- **L518** `intconst` n -> n+1 — `376bec7687a2`
  - `milestone_floor: int = -1`
  - `milestone_floor: int = -2`
- **L518** `intconst` n -> n-1 — `0ef559fb525f`
  - `milestone_floor: int = -1`
  - `milestone_floor: int = -0`
- **L523** `boolconst` False -> True — `9f977b951bb5`
  - `kidding_watched: bool = False`
  - `kidding_watched: bool = True`
- **L527** `boolconst` False -> True — `3b733d762885`
  - `dependent_kid: bool = False`
  - `dependent_kid: bool = True`
- **L689** `binop` Add -> Sub — `4d2f4843f907`
  - `animal.rebreed_from_day = day + _RESTING_FLUSH_DAYS`
  - `animal.rebreed_from_day = day - _RESTING_FLUSH_DAYS`
- **L751** `compare` Gt -> GtE — `31f0bf3cf4c1`
  - `assert kg_per_head > 0, 'banded creep group with a zero ration'`
  - `assert kg_per_head >= 0, 'banded creep group with a zero ration'`
- **L755** `intconst` n -> n-1 — `b1aec3c32e41`
  - `morning = round(daily * SHIFT_SPLIT[FeedingShift.MORNING], 3)`
  - `morning = round(daily * SHIFT_SPLIT[FeedingShift.MORNING], 2)`
- **L756** `intconst` n -> n+1 — `5e19e63865f6`
  - `afternoon = round(daily * SHIFT_SPLIT[FeedingShift.AFTERNOON], 3)`
  - `afternoon = round(daily * SHIFT_SPLIT[FeedingShift.AFTERNOON], 4)`
- **L756** `intconst` n -> n-1 — `192f3437be0d`
  - `afternoon = round(daily * SHIFT_SPLIT[FeedingShift.AFTERNOON], 3)`
  - `afternoon = round(daily * SHIFT_SPLIT[FeedingShift.AFTERNOON], 2)`
- **L757** `intconst` n -> n+1 — `6508dfeec4a1`
  - `night = round(daily * SHIFT_SPLIT[FeedingShift.NIGHT], 3)`
  - `night = round(daily * SHIFT_SPLIT[FeedingShift.NIGHT], 4)`
- **L757** `intconst` n -> n-1 — `f63671fe5023`
  - `night = round(daily * SHIFT_SPLIT[FeedingShift.NIGHT], 3)`
  - `night = round(daily * SHIFT_SPLIT[FeedingShift.NIGHT], 2)`
- **L776** `intconst` n -> n+1 — `e3de16ea4590`
  - `lines = self.days[day - 1].feeding`
  - `lines = self.days[day - 2].feeding`
- **L784** `intconst` n -> n+1 — `2e3241cdbcb3`
  - `by_recipe[line.recipe] = round(by_recipe.get(line.recipe, 0.0) + line.daily_kg, 3)`
  - `by_recipe[line.recipe] = round(by_recipe.get(line.recipe, 0.0) + line.daily_kg, 4)`
- **L784** `intconst` n -> n-1 — `64ba9592a488`
  - `by_recipe[line.recipe] = round(by_recipe.get(line.recipe, 0.0) + line.daily_kg, 3)`
  - `by_recipe[line.recipe] = round(by_recipe.get(line.recipe, 0.0) + line.daily_kg, 2)`
- **L889** `loopjump` continue -> break — `a98eb50c4d45`
  - `continue`
  - `break`
- **L905** `compare` Lt -> LtE — `474efba4dc08`
  - `conceived = self.rng.random() < self.params.conception_rate`
  - `conceived = self.rng.random() <= self.params.conception_rate`
- **L908** `intconst` n -> n+1 — `095b77d51f8e`
  - `animal.failed_services = 0`
  - `animal.failed_services = 1`
- **L926** `binop` Add -> Sub — `e13566d66060`
  - `self._record(day, TIME_DUTIES, 'ULTRASOUND', scan_building, [animal.tag], f'{animal.tag}: scan POSITIVE', f'Conceived (draw < {self.params.conception_rate:.2f}); expected kidding {self._date(animal.bred_day + _PROFILE.gestation_days)}.')`
  - `self._record(day, TIME_DUTIES, 'ULTRASOUND', scan_building, [animal.tag], f'{animal.tag}: scan POSITIVE', f'Conceived (draw < {self.params.conception_rate:.2f}); expected kidding {self._date(animal.bred_day - _PROFILE.gestation_days)}.')`
- **L931** `intconst` n -> n+1 — `ffac9d9dfdf1`
  - `self.failed_services += 1`
  - `self.failed_services += 2`
- **L931** `intconst` n -> n-1 — `b835b3ca94ee`
  - `self.failed_services += 1`
  - `self.failed_services += 0`
- **L989** `loopjump` continue -> break — `97f9e84df0bb`
  - `continue`
  - `break`
- **L996** `boolconst` True -> False — `0c2769cba9ad`
  - `animal.moved_to_late = True`
  - `animal.moved_to_late = False`
- **L1007** `binop` Add -> Sub — `15e1f2c44a69`
  - `ekd = animal.bred_day + _PROFILE.gestation_days`
  - `ekd = animal.bred_day - _PROFILE.gestation_days`
- **L1037** `ifexp` swap branches — `87f9c7734ec5`
  - `self._record(day, TIME_DUTIES, 'VACCINE', animal.bucket, [animal.tag], f'Pre-kidding ET+TT vaccine booster: {animal.tag}', 'Booster 15 days after the primary dose.' if animal.vaccine_primary_done else 'Booster only — her primary dose pre-dates this run.')`
  - `self._record(day, TIME_DUTIES, 'VACCINE', animal.bucket, [animal.tag], f'Pre-kidding ET+TT vaccine booster: {animal.tag}', 'Booster only — her primary dose pre-dates this run.' if animal.vaccine_primary_done else 'Booster 15 days after the primary dose.')`
- **L1067** `boolconst` True -> False — `da4e8a84d7b4`
  - `animal.kidding_watched = True`
  - `animal.kidding_watched = False`
- **L1090** `loopjump` continue -> break — `fa8e7feb43cb`
  - `continue`
  - `break`
- **L1129** `compare` GtE -> Gt — `51786314f19d`
  - `ready_gate = animal.age_months(day) >= _PROFILE.min_breeding_age_months`
  - `ready_gate = animal.age_months(day) > _PROFILE.min_breeding_age_months`
- **L1132** `ifexp` swap branches — `318ff6e5d992`
  - `reason = f'Flush complete ({_RESTING_FLUSH_DAYS} days in RESTING)' if animal.bucket == Bucket.RESTING.value else f'Breeding-ready at ~{animal.age_months(day):.1f} months'`
  - `reason = f'Breeding-ready at ~{animal.age_months(day):.1f} months' if animal.bucket == Bucket.RESTING.value else f'Flush complete ({_RESTING_FLUSH_DAYS} days in RESTING)'`
- **L1133** `compare` Eq -> NotEq — `276b51ca7bf1`
  - `reason = f'Flush complete ({_RESTING_FLUSH_DAYS} days in RESTING)' if animal.bucket == Bucket.RESTING.value else f'Breeding-ready at ~{animal.age_months(day):.1f} months'`
  - `reason = f'Flush complete ({_RESTING_FLUSH_DAYS} days in RESTING)' if animal.bucket != Bucket.RESTING.value else f'Breeding-ready at ~{animal.age_months(day):.1f} months'`
- **L1139** `loopjump` continue -> break — `906f1a74d5c6`
  - `continue`
  - `break`
- **L1141** `loopjump` continue -> break — `2d804382d060`
  - `continue`
  - `break`
- **L1152** `compare` GtE -> Gt — `a21374458244`
  - `bucks = [a for a in sorted(self._active(), key=lambda a: a.tag) if a.sex == 'M' and a.bucket == Bucket.BREEDING.value and (a.age_months(day) >= _PROFILE.min_sire_breeding_age_months)]`
  - `bucks = [a for a in sorted(self._active(), key=lambda a: a.tag) if a.sex == 'M' and a.bucket == Bucket.BREEDING.value and (a.age_months(day) > _PROFILE.min_sire_breeding_age_months)]`
- **L1168** `intconst` n -> n+1 — `4e891529e305`
  - `open_load[a.assigned_buck] += 1`
  - `open_load[a.assigned_buck] += 2`
- **L1184** `loopjump` continue -> break — `7bb478154f1a`
  - `continue`
  - `break`
- **L1192** `intconst` n -> n+1 — `d915daad8554`
  - `doe.milestone_floor = -1`
  - `doe.milestone_floor = -2`
- **L1192** `intconst` n -> n-1 — `e4279565c040`
  - `doe.milestone_floor = -1`
  - `doe.milestone_floor = -0`
- **L1209** `binop` Add -> Sub — `87262832c74c`
  - `self._record(day, TIME_DUTIES, 'OTHER', doe.bucket, [doe.tag, buck.tag], f'Breed {doe.tag} — sire {buck.tag}', f'Natural service; pregnancy check due {self._date(day + _PROFILE.pregnancy_check_after_service_days)}.')`
  - `self._record(day, TIME_DUTIES, 'OTHER', doe.bucket, [doe.tag, buck.tag], f'Breed {doe.tag} — sire {buck.tag}', f'Natural service; pregnancy check due {self._date(day - _PROFILE.pregnancy_check_after_service_days)}.')`
- **L1219** `loopjump` continue -> break — `8cd8e1fd2151`
  - `continue`
  - `break`
- **L1230** `compare` Lt -> LtE — `ee59b2783873`
  - `female = self.rng.random() < self.params.female_fraction_at_birth`
  - `female = self.rng.random() <= self.params.female_fraction_at_birth`
- **L1232** `compare` Lt -> LtE — `ef97cbbed83d`
  - `stillborn = self.rng.random() < self.params.stillbirth_rate`
  - `stillborn = self.rng.random() <= self.params.stillbirth_rate`
- **L1238** `loopjump` continue -> break — `dc8b1f9b1369`
  - `continue`
  - `break`
- **L1316** `boolconst` False -> True — `f1e6aa870a13`
  - `kid.dependent_kid = False`
  - `kid.dependent_kid = True`
- **L1506** `boolop` Or -> And — `9bd0b7db8e3e`
  - `key = (move.from_bucket or '', move.to_bucket, move.context)`
  - `key = (move.from_bucket and '', move.to_bucket, move.context)`
- **L1509** `intconst` n -> n+1 — `b78d07d560c0`
  - `transition_counts = [TransitionCount(from_bucket=k[0], to_bucket=k[1], context=k[2], count=v) for k, v in sorted(transition_counter.items())]`
  - `transition_counts = [TransitionCount(from_bucket=k[1], to_bucket=k[1], context=k[2], count=v) for k, v in sorted(transition_counter.items())]`
- **L1509** `intconst` n -> n+1 — `db6e19386958`
  - `transition_counts = [TransitionCount(from_bucket=k[0], to_bucket=k[1], context=k[2], count=v) for k, v in sorted(transition_counter.items())]`
  - `transition_counts = [TransitionCount(from_bucket=k[0], to_bucket=k[2], context=k[2], count=v) for k, v in sorted(transition_counter.items())]`
- **L1509** `intconst` n -> n-1 — `3ae7c6006cf2`
  - `transition_counts = [TransitionCount(from_bucket=k[0], to_bucket=k[1], context=k[2], count=v) for k, v in sorted(transition_counter.items())]`
  - `transition_counts = [TransitionCount(from_bucket=k[0], to_bucket=k[0], context=k[2], count=v) for k, v in sorted(transition_counter.items())]`
- **L1509** `intconst` n -> n-1 — `2c41e391b252`
  - `transition_counts = [TransitionCount(from_bucket=k[0], to_bucket=k[1], context=k[2], count=v) for k, v in sorted(transition_counter.items())]`
  - `transition_counts = [TransitionCount(from_bucket=k[0], to_bucket=k[1], context=k[1], count=v) for k, v in sorted(transition_counter.items())]`
- **L1521** `intconst` n -> n+1 — `c05981776def`
  - `feed_by_recipe[line.recipe] = round(feed_by_recipe.get(line.recipe, 0.0) + line.daily_kg, 3)`
  - `feed_by_recipe[line.recipe] = round(feed_by_recipe.get(line.recipe, 0.0) + line.daily_kg, 4)`
- **L1521** `intconst` n -> n-1 — `4341e1a26a22`
  - `feed_by_recipe[line.recipe] = round(feed_by_recipe.get(line.recipe, 0.0) + line.daily_kg, 3)`
  - `feed_by_recipe[line.recipe] = round(feed_by_recipe.get(line.recipe, 0.0) + line.daily_kg, 2)`
- **L1527** `binop` Add -> Sub — `de4ba74b5b32`
  - `vet_by_building[task.building] = vet_by_building.get(task.building, 0) + 1`
  - `vet_by_building[task.building] = vet_by_building.get(task.building, 0) - 1`
- **L1527** `intconst` n -> n+1 — `0cd9aa4f2b7b`
  - `vet_by_building[task.building] = vet_by_building.get(task.building, 0) + 1`
  - `vet_by_building[task.building] = vet_by_building.get(task.building, 0) + 2`
- **L1527** `intconst` n -> n-1 — `e2961bacf4ae`
  - `vet_by_building[task.building] = vet_by_building.get(task.building, 0) + 1`
  - `vet_by_building[task.building] = vet_by_building.get(task.building, 0) + 0`
- **L1527** `intconst` n -> n+1 — `fb2264d3a76a`
  - `vet_by_building[task.building] = vet_by_building.get(task.building, 0) + 1`
  - `vet_by_building[task.building] = vet_by_building.get(task.building, 1) + 1`
- **L1575** `ifexp` swap branches — `81b1ea8b77f4`
  - `head_end = sum((r.heads for r in result.days[-1].occupancy)) if result.days else 0`
  - `head_end = 0 if result.days else sum((r.heads for r in result.days[-1].occupancy))`
- **L1575** `intconst` n -> n+1 — `12d333b77970`
  - `head_end = sum((r.heads for r in result.days[-1].occupancy)) if result.days else 0`
  - `head_end = sum((r.heads for r in result.days[-1].occupancy)) if result.days else 1`
- **L1578** `intconst` n -> n+1 — `2e5bec6ed59f`
  - `busiest_vet_building = GOAT_BUILDING_NAMES.get(max(totals.vet_tasks_by_building.items(), key=lambda kv: kv[1])[0], '—') if totals.vet_tasks_by_building else '— (no vet duties this run)'`
  - `busiest_vet_building = GOAT_BUILDING_NAMES.get(max(totals.vet_tasks_by_building.items(), key=lambda kv: kv[1])[1], '—') if totals.vet_tasks_by_building else '— (no vet duties this run)'`
- **L1578** `intconst` n -> n-1 — `26e01c5da68b`
  - `busiest_vet_building = GOAT_BUILDING_NAMES.get(max(totals.vet_tasks_by_building.items(), key=lambda kv: kv[1])[0], '—') if totals.vet_tasks_by_building else '— (no vet duties this run)'`
  - `busiest_vet_building = GOAT_BUILDING_NAMES.get(max(totals.vet_tasks_by_building.items(), key=lambda kv: kv[0])[0], '—') if totals.vet_tasks_by_building else '— (no vet duties this run)'`
- **L1584** `boolop` Or -> And — `3b76d3fd0793`
  - `building_day_text = ', '.join((f'{GOAT_BUILDING_NAMES.get(b, b)} {d}' for b, d in totals.building_days.items())) or 'none'`
  - `building_day_text = ', '.join((f'{GOAT_BUILDING_NAMES.get(b, b)} {d}' for b, d in totals.building_days.items())) and 'none'`
- **L1709** `binop` Add -> Sub — `79f70795e452`
  - `notes = [f'Goat farm simulation (species={result.species}).', f'Breeding eligibility is age-gated (≥{_PROFILE.min_breeding_age_months} months); the live 22 kg weight gate is not modelled.', f'Meat sales fire entering the {run.params.male_sale_age_months}–{run.params.male_sale_age_months + 1} month window; the 24–28 kg weight band is not modelled.', 'Quarantine follows the seeded 45-day protocol; day-45 release moves the animal to FOUNDATION.', f'A failed scan re-serves the doe on her next heat (21 days); {run.params.failed_services_before_cull} consecutive failures cull her.']`
  - `notes = [f'Goat farm simulation (species={result.species}).', f'Breeding eligibility is age-gated (≥{_PROFILE.min_breeding_age_months} months); the live 22 kg weight gate is not modelled.', f'Meat sales fire entering the {run.params.male_sale_age_months}–{run.params.male_sale_age_months - 1} month window; the 24–28 kg weight band is not modelled.', 'Quarantine follows the seeded 45-day protocol; day-45 release moves the animal to FOUNDATION.', f'A failed scan re-serves the doe on her next heat (21 days); {run.params.failed_services_before_cull} consecutive failures cull her.']`
- **L1709** `intconst` n -> n+1 — `2276d59b95bc`
  - `notes = [f'Goat farm simulation (species={result.species}).', f'Breeding eligibility is age-gated (≥{_PROFILE.min_breeding_age_months} months); the live 22 kg weight gate is not modelled.', f'Meat sales fire entering the {run.params.male_sale_age_months}–{run.params.male_sale_age_months + 1} month window; the 24–28 kg weight band is not modelled.', 'Quarantine follows the seeded 45-day protocol; day-45 release moves the animal to FOUNDATION.', f'A failed scan re-serves the doe on her next heat (21 days); {run.params.failed_services_before_cull} consecutive failures cull her.']`
  - `notes = [f'Goat farm simulation (species={result.species}).', f'Breeding eligibility is age-gated (≥{_PROFILE.min_breeding_age_months} months); the live 22 kg weight gate is not modelled.', f'Meat sales fire entering the {run.params.male_sale_age_months}–{run.params.male_sale_age_months + 2} month window; the 24–28 kg weight band is not modelled.', 'Quarantine follows the seeded 45-day protocol; day-45 release moves the animal to FOUNDATION.', f'A failed scan re-serves the doe on her next heat (21 days); {run.params.failed_services_before_cull} consecutive failures cull her.']`
- **L1709** `intconst` n -> n-1 — `01de9fc6da36`
  - `notes = [f'Goat farm simulation (species={result.species}).', f'Breeding eligibility is age-gated (≥{_PROFILE.min_breeding_age_months} months); the live 22 kg weight gate is not modelled.', f'Meat sales fire entering the {run.params.male_sale_age_months}–{run.params.male_sale_age_months + 1} month window; the 24–28 kg weight band is not modelled.', 'Quarantine follows the seeded 45-day protocol; day-45 release moves the animal to FOUNDATION.', f'A failed scan re-serves the doe on her next heat (21 days); {run.params.failed_services_before_cull} consecutive failures cull her.']`
  - `notes = [f'Goat farm simulation (species={result.species}).', f'Breeding eligibility is age-gated (≥{_PROFILE.min_breeding_age_months} months); the live 22 kg weight gate is not modelled.', f'Meat sales fire entering the {run.params.male_sale_age_months}–{run.params.male_sale_age_months + 0} month window; the 24–28 kg weight band is not modelled.', 'Quarantine follows the seeded 45-day protocol; day-45 release moves the animal to FOUNDATION.', f'A failed scan re-serves the doe on her next heat (21 days); {run.params.failed_services_before_cull} consecutive failures cull her.']`
- **L1726** `compare` Eq -> NotEq — `4ba698970f81`
  - `standing_buck = any((spec.sex == 'M' and spec.bucket == Bucket.BREEDING.value and (spec.age_months >= GOAT_PROFILE.min_sire_breeding_age_months) for spec in run.payload.animals))`
  - `standing_buck = any((spec.sex == 'M' and spec.bucket != Bucket.BREEDING.value and (spec.age_months >= GOAT_PROFILE.min_sire_breeding_age_months) for spec in run.payload.animals))`
- **L1727** `compare` GtE -> Gt — `356bf9594ef3`
  - `standing_buck = any((spec.sex == 'M' and spec.bucket == Bucket.BREEDING.value and (spec.age_months >= GOAT_PROFILE.min_sire_breeding_age_months) for spec in run.payload.animals))`
  - `standing_buck = any((spec.sex == 'M' and spec.bucket == Bucket.BREEDING.value and (spec.age_months > GOAT_PROFILE.min_sire_breeding_age_months) for spec in run.payload.animals))`
- **L1796** `compare` Eq -> NotEq — `b2825d4ad0c8`
  - `building = FEED_STORE_NAME if line.building == FEED_STORE else GOAT_BUILDING_NAMES.get(line.building, line.building)`
  - `building = FEED_STORE_NAME if line.building != FEED_STORE else GOAT_BUILDING_NAMES.get(line.building, line.building)`
- **L1810** `ifexp` swap branches — `ea1d4bc7571e`
  - `building = FEED_STORE_NAME if task.building == FEED_STORE else GOAT_BUILDING_NAMES.get(task.building, task.building)`
  - `building = GOAT_BUILDING_NAMES.get(task.building, task.building) if task.building == FEED_STORE else FEED_STORE_NAME`
- **L1811** `compare` Eq -> NotEq — `e51cf25bf1f3`
  - `building = FEED_STORE_NAME if task.building == FEED_STORE else GOAT_BUILDING_NAMES.get(task.building, task.building)`
  - `building = FEED_STORE_NAME if task.building != FEED_STORE else GOAT_BUILDING_NAMES.get(task.building, task.building)`
- **L1814** `ifexp` swap branches — `8a41963a6103`
  - `animals = ', '.join(task.animals) if task.animals else '—'`
  - `animals = '—' if task.animals else ', '.join(task.animals)`
- **L1825** `boolop` Or -> And — `1662f251307a`
  - `lines.append(f"- **Move** {move.tag}: {move.from_bucket or '—'} → {move.to_bucket} ({move.context}) — {move.reason}")`
  - `lines.append(f"- **Move** {move.tag}: {move.from_bucket and '—'} → {move.to_bucket} ({move.context}) — {move.reason}")`
- **L1864** `boolop` Or -> And — `b0c563bcde3c`
  - `hops = ' → '.join((f'd{h.day} {h.to_bucket} ({h.context})' if h.from_bucket is None else f'd{h.day} {h.from_bucket}→{h.to_bucket} ({h.context})' for h in journey.hops)) or '—'`
  - `hops = ' → '.join((f'd{h.day} {h.to_bucket} ({h.context})' if h.from_bucket is None else f'd{h.day} {h.from_bucket}→{h.to_bucket} ({h.context})' for h in journey.hops)) and '—'`
- **L1865** `ifexp` swap branches — `51f83b3ed184`
  - `hops = ' → '.join((f'd{h.day} {h.to_bucket} ({h.context})' if h.from_bucket is None else f'd{h.day} {h.from_bucket}→{h.to_bucket} ({h.context})' for h in journey.hops)) or '—'`
  - `hops = ' → '.join((f'd{h.day} {h.from_bucket}→{h.to_bucket} ({h.context})' if h.from_bucket is None else f'd{h.day} {h.to_bucket} ({h.context})' for h in journey.hops)) or '—'`
- **L1866** `compare` Is -> IsNot — `4361426105cc`
  - `hops = ' → '.join((f'd{h.day} {h.to_bucket} ({h.context})' if h.from_bucket is None else f'd{h.day} {h.from_bucket}→{h.to_bucket} ({h.context})' for h in journey.hops)) or '—'`
  - `hops = ' → '.join((f'd{h.day} {h.to_bucket} ({h.context})' if h.from_bucket is not None else f'd{h.day} {h.from_bucket}→{h.to_bucket} ({h.context})' for h in journey.hops)) or '—'`
- **L1873** `ifexp` swap branches — `12f499f873e7`
  - `final = f'{journey.final_bucket} (active)' if journey.final_bucket else f'{journey.exit_kind} on day {journey.exit_day} — {journey.exit_reason}'`
  - `final = f'{journey.exit_kind} on day {journey.exit_day} — {journey.exit_reason}' if journey.final_bucket else f'{journey.final_bucket} (active)'`
- **L1878** `boolop` Or -> And — `6f76e313bef9`
  - `lines.append(f"| {journey.tag} | {journey.sex} | {journey.born_day or 'start'} | {hops} | {final} |")`
  - `lines.append(f"| {journey.tag} | {journey.sex} | {journey.born_day and 'start'} | {hops} | {final} |")`

### `app/simulation/defaults.py` (5 survivors)

- **L41** `binop` Sub -> Add — `5a42e4f3ad29`
  - `factor = (stall_fed[-1] - stall_fed[0]) / (base[-1] - base[0])`
  - `factor = (stall_fed[-1] + stall_fed[0]) / (base[-1] - base[0])`
- **L41** `binop` Sub -> Add — `e183c602cffc`
  - `factor = (stall_fed[-1] - stall_fed[0]) / (base[-1] - base[0])`
  - `factor = (stall_fed[-1] - stall_fed[0]) / (base[-1] + base[0])`
- **L41** `intconst` n -> n+1 — `e1c27a3b4ec9`
  - `factor = (stall_fed[-1] - stall_fed[0]) / (base[-1] - base[0])`
  - `factor = (stall_fed[-1] - stall_fed[1]) / (base[-1] - base[0])`
- **L41** `intconst` n -> n+1 — `7956ef8af45c`
  - `factor = (stall_fed[-1] - stall_fed[0]) / (base[-1] - base[0])`
  - `factor = (stall_fed[-2] - stall_fed[0]) / (base[-1] - base[0])`
- **L41** `intconst` n -> n-1 — `905e4ecc478a`
  - `factor = (stall_fed[-1] - stall_fed[0]) / (base[-1] - base[0])`
  - `factor = (stall_fed[-0] - stall_fed[0]) / (base[-1] - base[0])`

### `app/simulation/engine.py` (43 survivors)

- **L370** `boolconst` False -> True — `35984b797f24`
  - `def _pool_avg_weight(pool: list[float], base_age: int, growth: GrowthAssumptions, adult_weight_kg: float, fallback_age: int, *, male: bool=False) -> float:
    """Mean live weight of an age-indexed pool, for pricing a proportional draw.

    ``_draw`` removes head proportionally across every age slot, so a draw's
    mean weight is the pool's mean weight. Pricing it at one hard-coded
    mid-class age was only ever right for freshly *placed* stock: a pool filled
    organically by promotions has whatever distribution the run produced, and a
    grower chain spans up to 24 monthly slots. ``pool`` is indexed from
    ``base_age``; an empty pool has no composition to average, so it keeps the
    placement age (nothing is drawn from it anyway). ``male`` applies the
    young-male weight premium to the per-slot weights.
    """
    weight_fn = male_weight_at_age if male else weight_at_age
    total = sum(pool)
    if total <= 0.0:
        return weight_fn(fallback_age, growth, adult_weight_kg)
    return sum((count * weight_fn(base_age + offset, growth, adult_weight_kg) for offset, count in enumerate(pool))) / total`
  - `def _pool_avg_weight(pool: list[float], base_age: int, growth: GrowthAssumptions, adult_weight_kg: float, fallback_age: int, *, male: bool=True) -> float:
    """Mean live weight of an age-indexed pool, for pricing a proportional draw.

    ``_draw`` removes head proportionally across every age slot, so a draw's
    mean weight is the pool's mean weight. Pricing it at one hard-coded
    mid-class age was only ever right for freshly *placed* stock: a pool filled
    organically by promotions has whatever distribution the run produced, and a
    grower chain spans up to 24 monthly slots. ``pool`` is indexed from
    ``base_age``; an empty pool has no composition to average, so it keeps the
    placement age (nothing is drawn from it anyway). ``male`` applies the
    young-male weight premium to the per-slot weights.
    """
    weight_fn = male_weight_at_age if male else weight_at_age
    total = sum(pool)
    if total <= 0.0:
        return weight_fn(fallback_age, growth, adult_weight_kg)
    return sum((count * weight_fn(base_age + offset, growth, adult_weight_kg) for offset, count in enumerate(pool))) / total`
- **L688** `intconst` n -> n-1 — `19bb9bfdc735`
  - `settling_out = settling[-1]`
  - `settling_out = settling[-0]`
- **L689** `intconst` n -> n+1 — `2531b8505aed`
  - `settling = [0.0, *settling[:-1]]`
  - `settling = [0.0, *settling[:-2]]`
- **L689** `intconst` n -> n-1 — `06601fa7008e`
  - `settling = [0.0, *settling[:-1]]`
  - `settling = [0.0, *settling[:-0]]`
- **L720** `binop` Mult -> Div — `1daa5b460b23`
  - `s_kid = 1.0 - phase_monthly_mortality_rate(min(0.999999, mort.kid_pre_weaning * kid_mortality_shock), 3)`
  - `s_kid = 1.0 - phase_monthly_mortality_rate(min(0.999999, mort.kid_pre_weaning / kid_mortality_shock), 3)`
- **L723** `binop` Mult -> Div — `caffccb5d2ba`
  - `s_weaner = 1.0 - phase_monthly_mortality_rate(min(0.999999, mort.kid_post_weaning * kid_mortality_shock), 3)`
  - `s_weaner = 1.0 - phase_monthly_mortality_rate(min(0.999999, mort.kid_post_weaning / kid_mortality_shock), 3)`
- **L725** `binop` Mult -> Div — `5ef52f14f2b4`
  - `s_grower = 1.0 - monthly_mortality_rate(min(0.999999, mort.grower * adult_mortality_shock))`
  - `s_grower = 1.0 - monthly_mortality_rate(min(0.999999, mort.grower / adult_mortality_shock))`
- **L726** `binop` Mult -> Div — `7dcc321c5645`
  - `s_adult = 1.0 - monthly_mortality_rate(min(0.999999, mort.adult * adult_mortality_shock))`
  - `s_adult = 1.0 - monthly_mortality_rate(min(0.999999, mort.adult / adult_mortality_shock))`
- **L744** `intconst` n -> n+1 — `76b978d8ee59`
  - `svc[0] += n`
  - `svc[1] += n`
- **L761** `intconst` n -> n+1 — `4f97b5c9880c`
  - `f_weaner[age - 3] += n`
  - `f_weaner[age - 4] += n`
- **L765** `intconst` n -> n+1 — `414e1840f3e5`
  - `m_weaner[age - 3] += n`
  - `m_weaner[age - 4] += n`
- **L836** `intconst` n -> n+1 — `fdcafb282141`
  - `avg_kg = _pool_avg_weight(f_kid, 0, g, doe_w, 1)`
  - `avg_kg = _pool_avg_weight(f_kid, 0, g, doe_w, 2)`
- **L836** `intconst` n -> n-1 — `c5d2e5420c69`
  - `avg_kg = _pool_avg_weight(f_kid, 0, g, doe_w, 1)`
  - `avg_kg = _pool_avg_weight(f_kid, 0, g, doe_w, 0)`
- **L840** `intconst` n -> n+1 — `27539f199889`
  - `avg_kg = _pool_avg_weight(m_kid, 0, g, buck_w, 1, male=True)`
  - `avg_kg = _pool_avg_weight(m_kid, 0, g, buck_w, 2, male=True)`
- **L840** `intconst` n -> n-1 — `ea7eea303608`
  - `avg_kg = _pool_avg_weight(m_kid, 0, g, buck_w, 1, male=True)`
  - `avg_kg = _pool_avg_weight(m_kid, 0, g, buck_w, 0, male=True)`
- **L844** `intconst` n -> n+1 — `b03bac2c6fbd`
  - `avg_kg = _pool_avg_weight(f_weaner, 3, g, doe_w, 4)`
  - `avg_kg = _pool_avg_weight(f_weaner, 3, g, doe_w, 5)`
- **L844** `intconst` n -> n-1 — `43cbcb7c36eb`
  - `avg_kg = _pool_avg_weight(f_weaner, 3, g, doe_w, 4)`
  - `avg_kg = _pool_avg_weight(f_weaner, 3, g, doe_w, 3)`
- **L848** `intconst` n -> n+1 — `b1c6e7adac67`
  - `avg_kg = _pool_avg_weight(m_weaner, 3, g, buck_w, 4, male=True)`
  - `avg_kg = _pool_avg_weight(m_weaner, 3, g, buck_w, 5, male=True)`
- **L848** `intconst` n -> n-1 — `903ee243c79d`
  - `avg_kg = _pool_avg_weight(m_weaner, 3, g, buck_w, 4, male=True)`
  - `avg_kg = _pool_avg_weight(m_weaner, 3, g, buck_w, 3, male=True)`
- **L873** `binop` Sub -> Add — `ae843194f417`
  - `remaining = requested - take`
  - `remaining = requested + take`
- **L889** `binop` Sub -> Add — `52b64adbd0dd`
  - `held_factor = 1.0 - extra / held_total`
  - `held_factor = 1.0 + extra / held_total`
- **L889** `binop` Div -> Mult — `7edc353f2f7d`
  - `held_factor = 1.0 - extra / held_total`
  - `held_factor = 1.0 - extra * held_total`
- **L1009** `intconst` n -> n+1 — `19e125d3248c`
  - `svc[0] += lact_out`
  - `svc[1] += lact_out`
- **L1071** `binop` Add -> Sub — `579668becfdc`
  - `breeding_pool_before = ready_total + sum(open_waiting) + sum(settling) + sum(preg) + sum(lact)`
  - `breeding_pool_before = ready_total + sum(open_waiting) - sum(settling) + sum(preg) + sum(lact)`
- **L1208** `binop` Add -> Sub — `6c7d18413a9a`
  - `does_now = sum(svc) + sum(open_waiting) + sum(settling) + sum(preg) + sum(lact)`
  - `does_now = sum(svc) + sum(open_waiting) - sum(settling) + sum(preg) + sum(lact)`
- **L1209** `compare` Gt -> GtE — `2e5ce6f8f9c0`
  - `needed_bucks = _ceil_head_ratio(does_now, cull.buck_doe_ratio) if does_now > 0.0 else 0`
  - `needed_bucks = _ceil_head_ratio(does_now, cull.buck_doe_ratio) if does_now >= 0.0 else 0`
- **L1394** `binop` Mult -> Div — `f2434535de47`
  - `milk_litres_month = lact_total * sales.milk_sale_litres_per_doe_day * DAYS_PER_MONTH * (shocks.milk_yield[shock_index] if shocks.milk_yield else 1.0)`
  - `milk_litres_month = lact_total * sales.milk_sale_litres_per_doe_day * DAYS_PER_MONTH / (shocks.milk_yield[shock_index] if shocks.milk_yield else 1.0)`
- **L1397** `ifexp` swap branches — `a283953596b9`
  - `milk_litres_month = lact_total * sales.milk_sale_litres_per_doe_day * DAYS_PER_MONTH * (shocks.milk_yield[shock_index] if shocks.milk_yield else 1.0)`
  - `milk_litres_month = lact_total * sales.milk_sale_litres_per_doe_day * DAYS_PER_MONTH * (1.0 if shocks.milk_yield else shocks.milk_yield[shock_index])`
- **L1400** `binop` Mult -> Div — `17ccc45fee84`
  - `milk_revenue = milk_litres_month * sales.milk_price_per_litre * livestock_growth * (shocks.milk_price[shock_index] if shocks.milk_price else 1.0)`
  - `milk_revenue = milk_litres_month * sales.milk_price_per_litre * livestock_growth / (shocks.milk_price[shock_index] if shocks.milk_price else 1.0)`
- **L1403** `ifexp` swap branches — `59b3a394f6f0`
  - `milk_revenue = milk_litres_month * sales.milk_price_per_litre * livestock_growth * (shocks.milk_price[shock_index] if shocks.milk_price else 1.0)`
  - `milk_revenue = milk_litres_month * sales.milk_price_per_litre * livestock_growth * (1.0 if shocks.milk_price else shocks.milk_price[shock_index])`
- **L1615** `compare` Lt -> LtE — `d4e0407a3923`
  - `terminal_balance = schedule[horizon - 1].closing_balance if horizon < len(schedule) else 0.0`
  - `terminal_balance = schedule[horizon - 1].closing_balance if horizon <= len(schedule) else 0.0`
- **L1804** `intconst` n -> n-1 — `bab27e1a4a97`
  - `year = start // 12 + 1`
  - `year = start // 11 + 1`
- **L1830** `binop` Add -> Sub — `ddba6f43f04a`
  - `total_opex = feed_cost + vet + labour + insurance + misc + selling + purchases`
  - `total_opex = feed_cost + vet + labour + insurance + misc + selling - purchases`
- **L1908** `intconst` n -> n+1 — `bf173bf7363f`
  - `horizon_year = (horizon + 11) // 12`
  - `horizon_year = (horizon + 11) // 13`
- **L1908** `intconst` n -> n-1 — `242ca4f7a574`
  - `horizon_year = (horizon + 11) // 12`
  - `horizon_year = (horizon + 10) // 12`
- **L1943** `boolconst` True -> False — `c08af8ea049f`
  - `repaying_dscr = [value for value, row in zip(dscr_per_year, annual_pl, strict=True) if row.debt_service > 0.0 and _operating_principal(row) > 0.0]`
  - `repaying_dscr = [value for value, row in zip(dscr_per_year, annual_pl, strict=False) if row.debt_service > 0.0 and _operating_principal(row) > 0.0]`
- **L1944** `compare` Gt -> GtE — `607b574c4a1e`
  - `repaying_dscr = [value for value, row in zip(dscr_per_year, annual_pl, strict=True) if row.debt_service > 0.0 and _operating_principal(row) > 0.0]`
  - `repaying_dscr = [value for value, row in zip(dscr_per_year, annual_pl, strict=True) if row.debt_service >= 0.0 and _operating_principal(row) > 0.0]`
- **L2090** `boolconst` True -> False — `c2c7381c6fdc`
  - `def run_simulation(assumptions: SimulationAssumptions, *, with_break_even: bool=True, with_monte_carlo: bool=False, with_sensitivity: bool=False, with_optimization: bool=False, nouns: SpeciesNouns | None=None) -> SimulationResult:
    """Run the deterministic simulation and assemble the full result model.

    ``nouns`` supplies the species vocabulary for every human-readable string
    the result carries (narrative report, monthly event log). The product is
    goat-only, so this defaults to — and in practice always is — the goat
    set."""
    if nouns is None:
        nouns = GOAT_NOUNS
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    core = _run_core(assumptions, nouns=nouns)
    metrics = ViabilityMetrics(project_cost=core.project_cost, loan_amount=core.loan_amount, subsidy_amount=core.subsidy_amount, equity=core.equity, npv=core.npv, irr=core.irr, mirr=core.mirr, bcr=core.bcr, dscr_per_year=core.dscr_per_year, avg_dscr=core.avg_dscr, min_dscr=core.min_dscr, payback_month=core.payback_month, break_even_meat_price_per_kg=break_even_meat_price(assumptions) if with_break_even else None, peak_capacity_head=core.capacity_places, terminal_value=core.terminal_value, tax_total=core.tax_total, accounting_profit_total=core.accounting_profit_total, minimum_cash_balance=core.minimum_cash_balance, minimum_cash_month=core.minimum_cash_month, additional_working_capital_required=core.additional_working_capital_required, operating_margin=core.operating_margin)
    assumptions_payload = json.dumps(assumptions.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode('utf-8')
    warnings: list[str] = []
    festival_pricing_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month > 0
    if festival_pricing_active:
        start_year = int(assumptions.meta.start_year_month[:4])
        start_month_number = int(assumptions.meta.start_year_month[5:7])
        final_year = start_year + (start_month_number - 1 + assumptions.meta.horizon_months - 1) // 12
        last_covered_year = festival_coverage_last_year()
        if final_year > last_covered_year:
            warnings.append(f'Festival calendar covers through {last_covered_year}; months beyond that carry no Bakrid uplift.')
    result = SimulationResult(months=core.months, annual_pl=core.annual_pl, metrics=metrics, amortization=[AmortizationRowModel(month=row.month, opening_balance=row.opening_balance, payment=row.payment, interest=row.interest, principal=row.principal, closing_balance=row.closing_balance) for row in core.amortization], feed_summary=core.feed_summary, project_cost_breakdown=ProjectCostBreakdown(shed_cost=core.shed_cost, equipment_cost=core.equipment_cost, stock_cost=core.stock_cost, working_capital=core.working_capital, capacity_places=core.capacity_places, capacity_basis=assumptions.costs.capacity_basis, projected_peak_head=core.projected_peak_head), terminal_value_breakdown=core.terminal_value_breakdown, model_version=MODEL_VERSION, assumptions_fingerprint=hashlib.sha256(assumptions_payload).hexdigest(), warnings=warnings)
    if with_monte_carlo or with_sensitivity or with_optimization:
        from .montecarlo import run_monte_carlo, run_sensitivity
        if with_monte_carlo:
            result.monte_carlo = run_monte_carlo(assumptions)
        if with_sensitivity:
            result.sensitivity = run_sensitivity(assumptions)
        if with_optimization:
            from .optimization import run_optimization
            result.optimization = run_optimization(assumptions)
    from .explain import build_metric_explanations, build_narrative_report
    result.metric_explanations = build_metric_explanations(assumptions, result, break_even_computed=with_break_even, nouns=nouns)
    result.narrative_report = build_narrative_report(assumptions, result, nouns=nouns)
    return result`
  - `def run_simulation(assumptions: SimulationAssumptions, *, with_break_even: bool=False, with_monte_carlo: bool=False, with_sensitivity: bool=False, with_optimization: bool=False, nouns: SpeciesNouns | None=None) -> SimulationResult:
    """Run the deterministic simulation and assemble the full result model.

    ``nouns`` supplies the species vocabulary for every human-readable string
    the result carries (narrative report, monthly event log). The product is
    goat-only, so this defaults to — and in practice always is — the goat
    set."""
    if nouns is None:
        nouns = GOAT_NOUNS
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    core = _run_core(assumptions, nouns=nouns)
    metrics = ViabilityMetrics(project_cost=core.project_cost, loan_amount=core.loan_amount, subsidy_amount=core.subsidy_amount, equity=core.equity, npv=core.npv, irr=core.irr, mirr=core.mirr, bcr=core.bcr, dscr_per_year=core.dscr_per_year, avg_dscr=core.avg_dscr, min_dscr=core.min_dscr, payback_month=core.payback_month, break_even_meat_price_per_kg=break_even_meat_price(assumptions) if with_break_even else None, peak_capacity_head=core.capacity_places, terminal_value=core.terminal_value, tax_total=core.tax_total, accounting_profit_total=core.accounting_profit_total, minimum_cash_balance=core.minimum_cash_balance, minimum_cash_month=core.minimum_cash_month, additional_working_capital_required=core.additional_working_capital_required, operating_margin=core.operating_margin)
    assumptions_payload = json.dumps(assumptions.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode('utf-8')
    warnings: list[str] = []
    festival_pricing_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month > 0
    if festival_pricing_active:
        start_year = int(assumptions.meta.start_year_month[:4])
        start_month_number = int(assumptions.meta.start_year_month[5:7])
        final_year = start_year + (start_month_number - 1 + assumptions.meta.horizon_months - 1) // 12
        last_covered_year = festival_coverage_last_year()
        if final_year > last_covered_year:
            warnings.append(f'Festival calendar covers through {last_covered_year}; months beyond that carry no Bakrid uplift.')
    result = SimulationResult(months=core.months, annual_pl=core.annual_pl, metrics=metrics, amortization=[AmortizationRowModel(month=row.month, opening_balance=row.opening_balance, payment=row.payment, interest=row.interest, principal=row.principal, closing_balance=row.closing_balance) for row in core.amortization], feed_summary=core.feed_summary, project_cost_breakdown=ProjectCostBreakdown(shed_cost=core.shed_cost, equipment_cost=core.equipment_cost, stock_cost=core.stock_cost, working_capital=core.working_capital, capacity_places=core.capacity_places, capacity_basis=assumptions.costs.capacity_basis, projected_peak_head=core.projected_peak_head), terminal_value_breakdown=core.terminal_value_breakdown, model_version=MODEL_VERSION, assumptions_fingerprint=hashlib.sha256(assumptions_payload).hexdigest(), warnings=warnings)
    if with_monte_carlo or with_sensitivity or with_optimization:
        from .montecarlo import run_monte_carlo, run_sensitivity
        if with_monte_carlo:
            result.monte_carlo = run_monte_carlo(assumptions)
        if with_sensitivity:
            result.sensitivity = run_sensitivity(assumptions)
        if with_optimization:
            from .optimization import run_optimization
            result.optimization = run_optimization(assumptions)
    from .explain import build_metric_explanations, build_narrative_report
    result.metric_explanations = build_metric_explanations(assumptions, result, break_even_computed=with_break_even, nouns=nouns)
    result.narrative_report = build_narrative_report(assumptions, result, nouns=nouns)
    return result`
- **L2091** `boolconst` False -> True — `1cf56368d040`
  - `def run_simulation(assumptions: SimulationAssumptions, *, with_break_even: bool=True, with_monte_carlo: bool=False, with_sensitivity: bool=False, with_optimization: bool=False, nouns: SpeciesNouns | None=None) -> SimulationResult:
    """Run the deterministic simulation and assemble the full result model.

    ``nouns`` supplies the species vocabulary for every human-readable string
    the result carries (narrative report, monthly event log). The product is
    goat-only, so this defaults to — and in practice always is — the goat
    set."""
    if nouns is None:
        nouns = GOAT_NOUNS
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    core = _run_core(assumptions, nouns=nouns)
    metrics = ViabilityMetrics(project_cost=core.project_cost, loan_amount=core.loan_amount, subsidy_amount=core.subsidy_amount, equity=core.equity, npv=core.npv, irr=core.irr, mirr=core.mirr, bcr=core.bcr, dscr_per_year=core.dscr_per_year, avg_dscr=core.avg_dscr, min_dscr=core.min_dscr, payback_month=core.payback_month, break_even_meat_price_per_kg=break_even_meat_price(assumptions) if with_break_even else None, peak_capacity_head=core.capacity_places, terminal_value=core.terminal_value, tax_total=core.tax_total, accounting_profit_total=core.accounting_profit_total, minimum_cash_balance=core.minimum_cash_balance, minimum_cash_month=core.minimum_cash_month, additional_working_capital_required=core.additional_working_capital_required, operating_margin=core.operating_margin)
    assumptions_payload = json.dumps(assumptions.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode('utf-8')
    warnings: list[str] = []
    festival_pricing_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month > 0
    if festival_pricing_active:
        start_year = int(assumptions.meta.start_year_month[:4])
        start_month_number = int(assumptions.meta.start_year_month[5:7])
        final_year = start_year + (start_month_number - 1 + assumptions.meta.horizon_months - 1) // 12
        last_covered_year = festival_coverage_last_year()
        if final_year > last_covered_year:
            warnings.append(f'Festival calendar covers through {last_covered_year}; months beyond that carry no Bakrid uplift.')
    result = SimulationResult(months=core.months, annual_pl=core.annual_pl, metrics=metrics, amortization=[AmortizationRowModel(month=row.month, opening_balance=row.opening_balance, payment=row.payment, interest=row.interest, principal=row.principal, closing_balance=row.closing_balance) for row in core.amortization], feed_summary=core.feed_summary, project_cost_breakdown=ProjectCostBreakdown(shed_cost=core.shed_cost, equipment_cost=core.equipment_cost, stock_cost=core.stock_cost, working_capital=core.working_capital, capacity_places=core.capacity_places, capacity_basis=assumptions.costs.capacity_basis, projected_peak_head=core.projected_peak_head), terminal_value_breakdown=core.terminal_value_breakdown, model_version=MODEL_VERSION, assumptions_fingerprint=hashlib.sha256(assumptions_payload).hexdigest(), warnings=warnings)
    if with_monte_carlo or with_sensitivity or with_optimization:
        from .montecarlo import run_monte_carlo, run_sensitivity
        if with_monte_carlo:
            result.monte_carlo = run_monte_carlo(assumptions)
        if with_sensitivity:
            result.sensitivity = run_sensitivity(assumptions)
        if with_optimization:
            from .optimization import run_optimization
            result.optimization = run_optimization(assumptions)
    from .explain import build_metric_explanations, build_narrative_report
    result.metric_explanations = build_metric_explanations(assumptions, result, break_even_computed=with_break_even, nouns=nouns)
    result.narrative_report = build_narrative_report(assumptions, result, nouns=nouns)
    return result`
  - `def run_simulation(assumptions: SimulationAssumptions, *, with_break_even: bool=True, with_monte_carlo: bool=True, with_sensitivity: bool=False, with_optimization: bool=False, nouns: SpeciesNouns | None=None) -> SimulationResult:
    """Run the deterministic simulation and assemble the full result model.

    ``nouns`` supplies the species vocabulary for every human-readable string
    the result carries (narrative report, monthly event log). The product is
    goat-only, so this defaults to — and in practice always is — the goat
    set."""
    if nouns is None:
        nouns = GOAT_NOUNS
    assumptions = SimulationAssumptions.model_validate(assumptions.model_dump())
    core = _run_core(assumptions, nouns=nouns)
    metrics = ViabilityMetrics(project_cost=core.project_cost, loan_amount=core.loan_amount, subsidy_amount=core.subsidy_amount, equity=core.equity, npv=core.npv, irr=core.irr, mirr=core.mirr, bcr=core.bcr, dscr_per_year=core.dscr_per_year, avg_dscr=core.avg_dscr, min_dscr=core.min_dscr, payback_month=core.payback_month, break_even_meat_price_per_kg=break_even_meat_price(assumptions) if with_break_even else None, peak_capacity_head=core.capacity_places, terminal_value=core.terminal_value, tax_total=core.tax_total, accounting_profit_total=core.accounting_profit_total, minimum_cash_balance=core.minimum_cash_balance, minimum_cash_month=core.minimum_cash_month, additional_working_capital_required=core.additional_working_capital_required, operating_margin=core.operating_margin)
    assumptions_payload = json.dumps(assumptions.model_dump(mode='json'), sort_keys=True, separators=(',', ':')).encode('utf-8')
    warnings: list[str] = []
    festival_pricing_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month > 0
    if festival_pricing_active:
        start_year = int(assumptions.meta.start_year_month[:4])
        start_month_number = int(assumptions.meta.start_year_month[5:7])
        final_year = start_year + (start_month_number - 1 + assumptions.meta.horizon_months - 1) // 12
        last_covered_year = festival_coverage_last_year()
        if final_year > last_covered_year:
            warnings.append(f'Festival calendar covers through {last_covered_year}; months beyond that carry no Bakrid uplift.')
    result = SimulationResult(months=core.months, annual_pl=core.annual_pl, metrics=metrics, amortization=[AmortizationRowModel(month=row.month, opening_balance=row.opening_balance, payment=row.payment, interest=row.interest, principal=row.principal, closing_balance=row.closing_balance) for row in core.amortization], feed_summary=core.feed_summary, project_cost_breakdown=ProjectCostBreakdown(shed_cost=core.shed_cost, equipment_cost=core.equipment_cost, stock_cost=core.stock_cost, working_capital=core.working_capital, capacity_places=core.capacity_places, capacity_basis=assumptions.costs.capacity_basis, projected_peak_head=core.projected_peak_head), terminal_value_breakdown=core.terminal_value_breakdown, model_version=MODEL_VERSION, assumptions_fingerprint=hashlib.sha256(assumptions_payload).hexdigest(), warnings=warnings)
    if with_monte_carlo or with_sensitivity or with_optimization:
        from .montecarlo import run_monte_carlo, run_sensitivity
        if with_monte_carlo:
            result.monte_carlo = run_monte_carlo(assumptions)
        if with_sensitivity:
            result.sensitivity = run_sensitivity(assumptions)
        if with_optimization:
            from .optimization import run_optimization
            result.optimization = run_optimization(assumptions)
    from .explain import build_metric_explanations, build_narrative_report
    result.metric_explanations = build_metric_explanations(assumptions, result, break_even_computed=with_break_even, nouns=nouns)
    result.narrative_report = build_narrative_report(assumptions, result, nouns=nouns)
    return result`
- **L2146** `compare` Gt -> GtE — `632491066df3`
  - `festival_pricing_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month > 0`
  - `festival_pricing_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month >= 0`
- **L2146** `intconst` n -> n+1 — `61f2876b5671`
  - `festival_pricing_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month > 0`
  - `festival_pricing_active = bool(assumptions.sales.festival_sale_months) or assumptions.sales.eid_month > 1`
- **L2152** `intconst` n -> n+1 — `2e8c304b7ddc`
  - `final_year = start_year + (start_month_number - 1 + assumptions.meta.horizon_months - 1) // 12`
  - `final_year = start_year + (start_month_number - 1 + assumptions.meta.horizon_months - 1) // 13`
- **L2152** `intconst` n -> n+1 — `623a18a690d2`
  - `final_year = start_year + (start_month_number - 1 + assumptions.meta.horizon_months - 1) // 12`
  - `final_year = start_year + (start_month_number - 1 + assumptions.meta.horizon_months - 2) // 12`

### `app/simulation/explain.py` (6 survivors)

- **L334** `compare` Lt -> LtE — `360c25dc1f04`
  - `terminal_balance = result.amortization[horizon_months - 1].closing_balance if horizon_months < len(result.amortization) else 0.0`
  - `terminal_balance = result.amortization[horizon_months - 1].closing_balance if horizon_months <= len(result.amortization) else 0.0`
- **L851** `binop` Add -> Sub — `17235c431bea`
  - `total_opex = feed + vet + labour + insurance + misc + selling + stock_purchases`
  - `total_opex = feed + vet + labour + insurance + misc + selling - stock_purchases`
- **L866** `binop` Add -> Sub — `f1578c41b3fe`
  - `imputed = sum((labour_units_for(row.open_does + row.pregnant_does + row.lactating_does, False, a.costs.labour_per_head_threshold) for row in result.months)) * a.costs.labour_per_month`
  - `imputed = sum((labour_units_for(row.open_does + row.pregnant_does - row.lactating_does, False, a.costs.labour_per_head_threshold) for row in result.months)) * a.costs.labour_per_month`
- **L866** `binop` Add -> Sub — `892b0b61570f`
  - `imputed = sum((labour_units_for(row.open_does + row.pregnant_does + row.lactating_does, False, a.costs.labour_per_head_threshold) for row in result.months)) * a.costs.labour_per_month`
  - `imputed = sum((labour_units_for(row.open_does - row.pregnant_does + row.lactating_does, False, a.costs.labour_per_head_threshold) for row in result.months)) * a.costs.labour_per_month`
- **L867** `boolconst` False -> True — `c080a887c085`
  - `imputed = sum((labour_units_for(row.open_does + row.pregnant_does + row.lactating_does, False, a.costs.labour_per_head_threshold) for row in result.months)) * a.costs.labour_per_month`
  - `imputed = sum((labour_units_for(row.open_does + row.pregnant_does + row.lactating_does, True, a.costs.labour_per_head_threshold) for row in result.months)) * a.costs.labour_per_month`
- **L1052** `compare` Gt -> GtE — `54d315f19053`
  - `event_clauses = [f'{name} events' for name, probability in (('disease', risk.disease_outbreak_probability_annual), ('drought', risk.drought_probability_annual), ('market-crash', risk.market_crash_probability_annual)) if probability > 0.0]`
  - `event_clauses = [f'{name} events' for name, probability in (('disease', risk.disease_outbreak_probability_annual), ('drought', risk.drought_probability_annual), ('market-crash', risk.market_crash_probability_annual)) if probability >= 0.0]`

### `app/simulation/finance.py` (8 survivors)

- **L311** `binop` Sub -> Add — `3a9a8a7d4de0`
  - `derivative = [(exponent - 1, exponent * coefficient) for exponent, coefficient in terms if exponent != 0]`
  - `derivative = [(exponent + 1, exponent * coefficient) for exponent, coefficient in terms if exponent != 0]`
- **L311** `intconst` n -> n+1 — `5569c9e33c73`
  - `derivative = [(exponent - 1, exponent * coefficient) for exponent, coefficient in terms if exponent != 0]`
  - `derivative = [(exponent - 2, exponent * coefficient) for exponent, coefficient in terms if exponent != 0]`
- **L311** `intconst` n -> n-1 — `7f3351f73008`
  - `derivative = [(exponent - 1, exponent * coefficient) for exponent, coefficient in terms if exponent != 0]`
  - `derivative = [(exponent - 0, exponent * coefficient) for exponent, coefficient in terms if exponent != 0]`
- **L319** `boolconst` True -> False — `5af3de809586`
  - `roots = [point for point, sign in zip(points, signs, strict=True) if sign == 0]`
  - `roots = [point for point, sign in zip(points, signs, strict=False) if sign == 0]`
- **L345** `ifexp` swap branches — `7a146a155673`
  - `signs = [1 if coefficient > 0 else -1 for _, coefficient in terms if coefficient != 0]`
  - `signs = [-1 if coefficient > 0 else 1 for _, coefficient in terms if coefficient != 0]`
- **L345** `compare` Gt -> GtE — `cc8439d7055e`
  - `signs = [1 if coefficient > 0 else -1 for _, coefficient in terms if coefficient != 0]`
  - `signs = [1 if coefficient >= 0 else -1 for _, coefficient in terms if coefficient != 0]`
- **L345** `intconst` n -> n+1 — `7b536d3ed888`
  - `signs = [1 if coefficient > 0 else -1 for _, coefficient in terms if coefficient != 0]`
  - `signs = [2 if coefficient > 0 else -1 for _, coefficient in terms if coefficient != 0]`
- **L345** `intconst` n -> n-1 — `d1783dc1a0f8`
  - `signs = [1 if coefficient > 0 else -1 for _, coefficient in terms if coefficient != 0]`
  - `signs = [0 if coefficient > 0 else -1 for _, coefficient in terms if coefficient != 0]`

### `app/simulation/market.py` (4 survivors)

- **L38** `compare` Gt -> GtE — `9ce403085cfa`
  - `festival = simulation_month in sales.festival_sale_months if sales.festival_sale_months is not None else sales.eid_month > 0 and calendar_month == sales.eid_month`
  - `festival = simulation_month in sales.festival_sale_months if sales.festival_sale_months is not None else sales.eid_month >= 0 and calendar_month == sales.eid_month`
- **L121** `intconst` n -> n+1 — `c4f024dfaded`
  - `BAKRID_DATES_BY_YEAR: dict[int, tuple[int, int]] = {2026: (5, 28), 2027: (5, 17), 2028: (5, 5), 2029: (4, 24), 2030: (4, 14), 2031: (4, 3), 2032: (3, 22), 2033: (3, 11), 2034: (2, 28), 2035: (2, 17), 2036: (2, 6), 2037: (1, 26), 2038: (1, 15), 2039: (1, 4), 2040: (12, 26), 2041: (12, 4), 2042: (11, 23), 2043: (11, 12), 2044: (10, 31), 2045: (10, 21), 2046: (10, 10), 2047: (9, 29), 2048: (9, 18), 2049: (9, 7), 2050: (8, 28)}`
  - `BAKRID_DATES_BY_YEAR: dict[int, tuple[int, int]] = {2026: (5, 28), 2027: (5, 17), 2028: (5, 5), 2029: (4, 24), 2030: (4, 14), 2031: (4, 3), 2032: (3, 22), 2033: (3, 11), 2034: (2, 28), 2035: (2, 17), 2036: (2, 6), 2037: (1, 26), 2038: (1, 15), 2039: (1, 4), 2040: (13, 26), 2041: (12, 4), 2042: (11, 23), 2043: (11, 12), 2044: (10, 31), 2045: (10, 21), 2046: (10, 10), 2047: (9, 29), 2048: (9, 18), 2049: (9, 7), 2050: (8, 28)}`
- **L121** `intconst` n -> n-1 — `c28d2fa378e2`
  - `BAKRID_DATES_BY_YEAR: dict[int, tuple[int, int]] = {2026: (5, 28), 2027: (5, 17), 2028: (5, 5), 2029: (4, 24), 2030: (4, 14), 2031: (4, 3), 2032: (3, 22), 2033: (3, 11), 2034: (2, 28), 2035: (2, 17), 2036: (2, 6), 2037: (1, 26), 2038: (1, 15), 2039: (1, 4), 2040: (12, 26), 2041: (12, 4), 2042: (11, 23), 2043: (11, 12), 2044: (10, 31), 2045: (10, 21), 2046: (10, 10), 2047: (9, 29), 2048: (9, 18), 2049: (9, 7), 2050: (8, 28)}`
  - `BAKRID_DATES_BY_YEAR: dict[int, tuple[int, int]] = {2026: (5, 28), 2027: (5, 17), 2028: (5, 5), 2029: (4, 24), 2030: (4, 14), 2031: (4, 3), 2032: (3, 22), 2033: (3, 11), 2034: (2, 28), 2035: (2, 17), 2036: (2, 6), 2037: (1, 26), 2038: (1, 15), 2039: (1, 4), 2040: (11, 26), 2041: (12, 4), 2042: (11, 23), 2043: (11, 12), 2044: (10, 31), 2045: (10, 21), 2046: (10, 10), 2047: (9, 29), 2048: (9, 18), 2049: (9, 7), 2050: (8, 28)}`
- **L128** `intconst` n -> n+1 — `a06dfc484648`
  - `BAKRID_DATES_BY_YEAR: dict[int, tuple[int, int]] = {2026: (5, 28), 2027: (5, 17), 2028: (5, 5), 2029: (4, 24), 2030: (4, 14), 2031: (4, 3), 2032: (3, 22), 2033: (3, 11), 2034: (2, 28), 2035: (2, 17), 2036: (2, 6), 2037: (1, 26), 2038: (1, 15), 2039: (1, 4), 2040: (12, 26), 2041: (12, 4), 2042: (11, 23), 2043: (11, 12), 2044: (10, 31), 2045: (10, 21), 2046: (10, 10), 2047: (9, 29), 2048: (9, 18), 2049: (9, 7), 2050: (8, 28)}`
  - `BAKRID_DATES_BY_YEAR: dict[int, tuple[int, int]] = {2026: (5, 28), 2027: (5, 17), 2028: (5, 5), 2029: (4, 24), 2030: (4, 14), 2031: (4, 3), 2032: (3, 22), 2033: (3, 11), 2034: (2, 28), 2035: (2, 17), 2036: (2, 6), 2037: (1, 26), 2038: (1, 15), 2039: (1, 4), 2040: (12, 26), 2041: (12, 4), 2042: (11, 23), 2043: (11, 12), 2044: (10, 31), 2045: (10, 21), 2046: (10, 10), 2047: (9, 30), 2048: (9, 18), 2049: (9, 7), 2050: (8, 28)}`

### `app/simulation/montecarlo.py` (2 survivors)

- **L189** `loopjump` continue -> break — `22077e24dd3f`
  - `continue`
  - `break`
- **L635** `intconst` n -> n-1 — `36b1e4f955af`
  - `cases: list[_SensitivityCase] = [_SensitivityCase('meat_price', lambda v: v.sales.meat_price_per_kg, _pct_label, lambda v: setattr(v.sales, 'meat_price_per_kg', v.sales.meat_price_per_kg * 0.8), lambda v: setattr(v.sales, 'meat_price_per_kg', min(MAX_MONEY, v.sales.meat_price_per_kg * 1.2))), _SensitivityCase('milk_price', lambda v: v.sales.milk_price_per_litre, _pct_label, _scale_milk_price, _scale_milk_price_high), _SensitivityCase('feed_prices', lambda v: v.feed.green_price_per_kg + v.feed.purchased_green_price_per_kg + v.feed.dry_price_per_kg + v.feed.concentrate_price_per_kg, _pct_label, lambda v: _scale_feed_prices(v, 0.8), lambda v: _scale_feed_prices(v, 1.2)), _SensitivityCase('kid_pre_weaning_mortality', lambda v: v.mortality.kid_pre_weaning, _pct_label, lambda v: setattr(v.mortality, 'kid_pre_weaning', v.mortality.kid_pre_weaning * 0.8), lambda v: setattr(v.mortality, 'kid_pre_weaning', min(0.9, v.mortality.kid_pre_weaning * 1.2))), _SensitivityCase('litter_size', lambda v: v.reproduction.litter_size, _pct_label, lambda v: setattr(v.reproduction, 'litter_size', max(0.5, v.reproduction.litter_size * 0.8)), lambda v: setattr(v.reproduction, 'litter_size', min(_MAX_LITTER, v.reproduction.litter_size * 1.2))), _SensitivityCase('conception_rate', lambda v: v.reproduction.conception_rate, _pct_label, lambda v: setattr(v.reproduction, 'conception_rate', v.reproduction.conception_rate * 0.8), lambda v: setattr(v.reproduction, 'conception_rate', min(1.0, v.reproduction.conception_rate * 1.2))), _SensitivityCase('sale_age_months', lambda v: float(v.growth.sale_age_months), _months_label, lambda v: setattr(v.growth, 'sale_age_months', max(min_feasible_sale_age(v), v.growth.sale_age_months - 2)), lambda v: setattr(v.growth, 'sale_age_months', min(24, v.growth.sale_age_months + 2))), _SensitivityCase('labour_cost', lambda v: v.costs.labour_per_month, _pct_label, lambda v: setattr(v.costs, 'labour_per_month', v.costs.labour_per_month * 0.8), lambda v: setattr(v.costs, 'labour_per_month', min(MAX_MONEY, v.costs.labour_per_month * 1.2))), _SensitivityCase('interest_rate', lambda v: v.finance.interest_rate_annual, _pct_label, lambda v: setattr(v.finance, 'interest_rate_annual', v.finance.interest_rate_annual * 0.8), lambda v: setattr(v.finance, 'interest_rate_annual', min(0.5, v.finance.interest_rate_annual * 1.2)))]`
  - `cases: list[_SensitivityCase] = [_SensitivityCase('meat_price', lambda v: v.sales.meat_price_per_kg, _pct_label, lambda v: setattr(v.sales, 'meat_price_per_kg', v.sales.meat_price_per_kg * 0.8), lambda v: setattr(v.sales, 'meat_price_per_kg', min(MAX_MONEY, v.sales.meat_price_per_kg * 1.2))), _SensitivityCase('milk_price', lambda v: v.sales.milk_price_per_litre, _pct_label, _scale_milk_price, _scale_milk_price_high), _SensitivityCase('feed_prices', lambda v: v.feed.green_price_per_kg + v.feed.purchased_green_price_per_kg + v.feed.dry_price_per_kg + v.feed.concentrate_price_per_kg, _pct_label, lambda v: _scale_feed_prices(v, 0.8), lambda v: _scale_feed_prices(v, 1.2)), _SensitivityCase('kid_pre_weaning_mortality', lambda v: v.mortality.kid_pre_weaning, _pct_label, lambda v: setattr(v.mortality, 'kid_pre_weaning', v.mortality.kid_pre_weaning * 0.8), lambda v: setattr(v.mortality, 'kid_pre_weaning', min(0.9, v.mortality.kid_pre_weaning * 1.2))), _SensitivityCase('litter_size', lambda v: v.reproduction.litter_size, _pct_label, lambda v: setattr(v.reproduction, 'litter_size', max(0.5, v.reproduction.litter_size * 0.8)), lambda v: setattr(v.reproduction, 'litter_size', min(_MAX_LITTER, v.reproduction.litter_size * 1.2))), _SensitivityCase('conception_rate', lambda v: v.reproduction.conception_rate, _pct_label, lambda v: setattr(v.reproduction, 'conception_rate', v.reproduction.conception_rate * 0.8), lambda v: setattr(v.reproduction, 'conception_rate', min(1.0, v.reproduction.conception_rate * 1.2))), _SensitivityCase('sale_age_months', lambda v: float(v.growth.sale_age_months), _months_label, lambda v: setattr(v.growth, 'sale_age_months', max(min_feasible_sale_age(v), v.growth.sale_age_months - 2)), lambda v: setattr(v.growth, 'sale_age_months', min(23, v.growth.sale_age_months + 2))), _SensitivityCase('labour_cost', lambda v: v.costs.labour_per_month, _pct_label, lambda v: setattr(v.costs, 'labour_per_month', v.costs.labour_per_month * 0.8), lambda v: setattr(v.costs, 'labour_per_month', min(MAX_MONEY, v.costs.labour_per_month * 1.2))), _SensitivityCase('interest_rate', lambda v: v.finance.interest_rate_annual, _pct_label, lambda v: setattr(v.finance, 'interest_rate_annual', v.finance.interest_rate_annual * 0.8), lambda v: setattr(v.finance, 'interest_rate_annual', min(0.5, v.finance.interest_rate_annual * 1.2)))]`

### `app/simulation/optimization.py` (7 survivors)

- **L37** `intconst` n -> n-1 — `e72ede13d0fb`
  - `tolerance = 8 * max(math.ulp(value), math.ulp(unique[-1])) if unique else 0.0`
  - `tolerance = 8 * max(math.ulp(value), math.ulp(unique[-0])) if unique else 0.0`
- **L142** `boolconst` True -> False — `78c42987b262`
  - `thinned = [_sample_evenly(list(axis), count) for axis, count in zip(axes, counts, strict=True)]`
  - `thinned = [_sample_evenly(list(axis), count) for axis, count in zip(axes, counts, strict=False)]`
- **L210** `intconst` n -> n+1 — `75a679533837`
  - `festival_holds = _int_axis(a.sales.festival_hold_months, policy.festival_hold_radius_months, 0, 12)`
  - `festival_holds = _int_axis(a.sales.festival_hold_months, policy.festival_hold_radius_months, 1, 12)`
- **L210** `intconst` n -> n+1 — `67a4b7b6257d`
  - `festival_holds = _int_axis(a.sales.festival_hold_months, policy.festival_hold_radius_months, 0, 12)`
  - `festival_holds = _int_axis(a.sales.festival_hold_months, policy.festival_hold_radius_months, 0, 13)`
- **L210** `intconst` n -> n-1 — `3402798c592c`
  - `festival_holds = _int_axis(a.sales.festival_hold_months, policy.festival_hold_radius_months, 0, 12)`
  - `festival_holds = _int_axis(a.sales.festival_hold_months, policy.festival_hold_radius_months, 0, 11)`
- **L213** `intconst` n -> n+1 — `9166a76a4a85`
  - `service_culls = _int_axis(a.reproduction.max_services_before_cull, policy.service_cull_radius_months, 0, 12)`
  - `service_culls = _int_axis(a.reproduction.max_services_before_cull, policy.service_cull_radius_months, 0, 13)`
- **L251** `compare` Gt -> GtE — `b7c12304b288`
  - `variant.herd.bucks = _ceil_head_ratio(variant.herd.does, variant.culling.buck_doe_ratio) if variant.herd.does > 0 else 0`
  - `variant.herd.bucks = _ceil_head_ratio(variant.herd.does, variant.culling.buck_doe_ratio) if variant.herd.does >= 0 else 0`

### `app/simulation/planner.py` (49 survivors)

- **L110** `intconst` n -> n-1 — `39c73fd2616f`
  - `ceiling = first_breeding_months if animal_class.startswith('female') else max(sale_age_months, 6)`
  - `ceiling = first_breeding_months if animal_class.startswith('female') else max(sale_age_months, 5)`
- **L121** `intconst` n -> n+1 — `9cfc5e2d4e4a`
  - `month: int = Field(ge=1)`
  - `month: int = Field(ge=2)`
- **L183** `boolconst` True -> False — `dc0f41c6d500`
  - `variant = assumptions.model_copy(deep=True)`
  - `variant = assumptions.model_copy(deep=False)`
- **L224** `ifexp` swap branches — `78efa46da654`
  - `results.append(TargetFill(month=target.month, animal_class=target.animal_class, requested=target.count, filled=fill.filled, shortfall=max(0.0, target.count - fill.filled), price_per_head=fill.price_per_head, revenue=fill.revenue if fill.filled > 0.0 else 0.0, met=fill.filled >= target.count - 0.5))`
  - `results.append(TargetFill(month=target.month, animal_class=target.animal_class, requested=target.count, filled=fill.filled, shortfall=max(0.0, target.count - fill.filled), price_per_head=fill.price_per_head, revenue=0.0 if fill.filled > 0.0 else fill.revenue, met=fill.filled >= target.count - 0.5))`
- **L225** `compare` GtE -> Gt — `e7784e687e44`
  - `results.append(TargetFill(month=target.month, animal_class=target.animal_class, requested=target.count, filled=fill.filled, shortfall=max(0.0, target.count - fill.filled), price_per_head=fill.price_per_head, revenue=fill.revenue if fill.filled > 0.0 else 0.0, met=fill.filled >= target.count - 0.5))`
  - `results.append(TargetFill(month=target.month, animal_class=target.animal_class, requested=target.count, filled=fill.filled, shortfall=max(0.0, target.count - fill.filled), price_per_head=fill.price_per_head, revenue=fill.revenue if fill.filled > 0.0 else 0.0, met=fill.filled > target.count - 0.5))`
- **L265** `intconst` n -> n+1 — `057adf6a476a`
  - `kid_months = min(completed_age_months + 1, 3)`
  - `kid_months = min(completed_age_months + 1, 4)`
- **L265** `intconst` n -> n-1 — `2e47368014d0`
  - `kid_months = min(completed_age_months + 1, 3)`
  - `kid_months = min(completed_age_months + 1, 2)`
- **L266** `intconst` n -> n+1 — `a486aa72a8e3`
  - `weaner_months = min(max(completed_age_months - 2, 0), 3)`
  - `weaner_months = min(max(completed_age_months - 2, 0), 4)`
- **L266** `intconst` n -> n-1 — `45ceb2d2c60a`
  - `weaner_months = min(max(completed_age_months - 2, 0), 3)`
  - `weaner_months = min(max(completed_age_months - 1, 0), 3)`
- **L303** `intconst` n -> n+1 — `9bd37a90ba65`
  - `earliest_birth = target.month - class_max_age - 1`
  - `earliest_birth = target.month - class_max_age - 2`
- **L304** `binop` Sub -> Add — `73d2ef2bb925`
  - `latest_birth = target.month - entry_age - 1`
  - `latest_birth = target.month - entry_age + 1`
- **L304** `binop` Sub -> Add — `9e61387eeff5`
  - `latest_birth = target.month - entry_age - 1`
  - `latest_birth = target.month + entry_age - 1`
- **L304** `intconst` n -> n-1 — `5d9c44570e84`
  - `latest_birth = target.month - entry_age - 1`
  - `latest_birth = target.month - entry_age - 0`
- **L312** `binop` Sub -> Add — `226689a0f700`
  - `monthly_cull = 1.0 - monthly_mortality_rate(cull.doe_cull_rate_annual)`
  - `monthly_cull = 1.0 + monthly_mortality_rate(cull.doe_cull_rate_annual)`
- **L314** `binop` Add -> Sub — `a7bd04a8544e`
  - `first_service = purchase_month + herd.purchased_doe_settling_months`
  - `first_service = purchase_month - herd.purchased_doe_settling_months`
- **L341** `binop` Sub -> Add — `3b7d6b06c1fe`
  - `female_per_birth = r.litter_size * (1.0 - r.stillbirth_rate) * r.sex_ratio_female`
  - `female_per_birth = r.litter_size * (1.0 + r.stillbirth_rate) * r.sex_ratio_female`
- **L344** `binop` Sub -> Add — `dd78213b8b47`
  - `survival_to_afb = _survival_to_event_age(mort, afb - 1)`
  - `survival_to_afb = _survival_to_event_age(mort, afb + 1)`
- **L346** `intconst` n -> n+1 — `fb3d47e67887`
  - `last_month = latest_birth + 1`
  - `last_month = latest_birth + 2`
- **L346** `intconst` n -> n-1 — `708a141f00bb`
  - `last_month = latest_birth + 1`
  - `last_month = latest_birth + 0`
- **L359** `intconst` n -> n-1 — `8fcbc4529b6e`
  - `ready[month_index + 1] = ready.get(month_index + 1, 0.0) + remaining`
  - `ready[month_index + 0] = ready.get(month_index + 1, 0.0) + remaining`
- **L380** `binop` Mult -> Div — `fce16b8550a8`
  - `per_birth = r.litter_size * (1.0 - r.stillbirth_rate) * female_share`
  - `per_birth = r.litter_size / (1.0 - r.stillbirth_rate) * female_share`
- **L384** `binop` Sub -> Add — `0d8b85e0680d`
  - `completed_age = target.month - birth_month - 1`
  - `completed_age = target.month - birth_month + 1`
- **L403** `intconst` n -> n+1 — `4156c7300e1a`
  - `lead = class_max_age + assumptions.reproduction.gestation_months + assumptions.herd.purchased_doe_settling_months + 1`
  - `lead = class_max_age + assumptions.reproduction.gestation_months + assumptions.herd.purchased_doe_settling_months + 2`
- **L435** `loopjump` continue -> break — `8238b0b21208`
  - `continue`
  - `break`
- **L486** `intconst` n -> n+1 — `35e3107f1ad5`
  - `reserved_events = sum((1 for event in assumptions.events if event.kind == 'purchase')) + len(targets)`
  - `reserved_events = sum((2 for event in assumptions.events if event.kind == 'purchase')) + len(targets)`
- **L500** `loopjump` break -> continue — `279bcbb89673`
  - `break`
  - `continue`
- **L501** `boolconst` False -> True — `9363aef78db8`
  - `changed = False`
  - `changed = True`
- **L505** `loopjump` continue -> break — `696fd128ed09`
  - `continue`
  - `break`
- **L508** `loopjump` continue -> break — `2f53556d158a`
  - `continue`
  - `break`
- **L522** `loopjump` continue -> break — `e7f4432143bd`
  - `continue`
  - `break`
- **L537** `loopjump` break -> continue — `0c75ac20e784`
  - `break`
  - `continue`
- **L592** `ifexp` swap branches — `5eeaa2df7ff6`
  - `reserved_note = f" ({reserved} events are already reserved by the plan's kept purchase/sale events)" if reserved else ''`
  - `reserved_note = '' if reserved else f" ({reserved} events are already reserved by the plan's kept purchase/sale events)"`
- **L651** `ifexp` swap branches — `758e2a8160a0`
  - `rng = random.Random(seed if seed is not None else assumptions.risk.seed)`
  - `rng = random.Random(assumptions.risk.seed if seed is not None else seed)`
- **L651** `compare` IsNot -> Is — `6cce67cc9189`
  - `rng = random.Random(seed if seed is not None else assumptions.risk.seed)`
  - `rng = random.Random(seed if seed is None else assumptions.risk.seed)`
- **L663** `intconst` n -> n+1 — `5d7beae3ad2d`
  - `full = [0] * len(targets)`
  - `full = [1] * len(targets)`
- **L664** `intconst` n -> n+1 — `0fa76e529b7d`
  - `eighty = [0] * len(targets)`
  - `eighty = [1] * len(targets)`
- **L665** `intconst` n -> n+1 — `0ec58c0ecb6e`
  - `completed = 0`
  - `completed = 1`
- **L682** `intconst` n -> n+1 — `6530bcd608d0`
  - `completed += 1`
  - `completed += 2`
- **L685** `intconst` n -> n+1 — `db1c78e08e04`
  - `full[index] += 1`
  - `full[index] += 2`
- **L685** `intconst` n -> n-1 — `560e7f8edca2`
  - `full[index] += 1`
  - `full[index] += 0`
- **L687** `intconst` n -> n+1 — `a42af2a4c7aa`
  - `eighty[index] += 1`
  - `eighty[index] += 2`
- **L688** `intconst` n -> n+1 — `4e35c7eda9b7`
  - `denominator = completed or 1`
  - `denominator = completed or 2`
- **L688** `intconst` n -> n-1 — `469de8216591`
  - `denominator = completed or 1`
  - `denominator = completed or 0`
- **L717** `binop` Div -> Mult — `961853428549`
  - `horizon_years = assumptions.meta.horizon_months / 12.0`
  - `horizon_years = assumptions.meta.horizon_months * 12.0`
- **L784** `ifexp` swap branches — `ae4c9f7cd7e2`
  - `lines += [f'## Viability ({horizon_years:g}-year projection)', '', '| Metric | Value |', '| --- | ---: |', f'| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |', f'| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |', f"| Benefit-cost ratio | {('n/a' if m.bcr is None else f'{m.bcr:.2f}')} |", f'| Average / weakest DSCR (repaying years) | {_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |', f"| Payback | {('beyond horizon' if m.payback_month is None else f'month {m.payback_month}')} |", '| Break-even meat price | ' + ('n/a' if m.break_even_meat_price_per_kg is None else f'₹{m.break_even_meat_price_per_kg:,.0f}/kg') + ' |', '', '## Annual operating summary', '', '| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |', '| ---: | ---: | ---: | ---: | ---: | ---: |']`
  - `lines += [f'## Viability ({horizon_years:g}-year projection)', '', '| Metric | Value |', '| --- | ---: |', f'| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |', f'| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |', f"| Benefit-cost ratio | {(f'{m.bcr:.2f}' if m.bcr is None else 'n/a')} |", f'| Average / weakest DSCR (repaying years) | {_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |', f"| Payback | {('beyond horizon' if m.payback_month is None else f'month {m.payback_month}')} |", '| Break-even meat price | ' + ('n/a' if m.break_even_meat_price_per_kg is None else f'₹{m.break_even_meat_price_per_kg:,.0f}/kg') + ' |', '', '## Annual operating summary', '', '| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |', '| ---: | ---: | ---: | ---: | ---: | ---: |']`
- **L784** `compare` Is -> IsNot — `9d131dbd525e`
  - `lines += [f'## Viability ({horizon_years:g}-year projection)', '', '| Metric | Value |', '| --- | ---: |', f'| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |', f'| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |', f"| Benefit-cost ratio | {('n/a' if m.bcr is None else f'{m.bcr:.2f}')} |", f'| Average / weakest DSCR (repaying years) | {_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |', f"| Payback | {('beyond horizon' if m.payback_month is None else f'month {m.payback_month}')} |", '| Break-even meat price | ' + ('n/a' if m.break_even_meat_price_per_kg is None else f'₹{m.break_even_meat_price_per_kg:,.0f}/kg') + ' |', '', '## Annual operating summary', '', '| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |', '| ---: | ---: | ---: | ---: | ---: | ---: |']`
  - `lines += [f'## Viability ({horizon_years:g}-year projection)', '', '| Metric | Value |', '| --- | ---: |', f'| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |', f'| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |', f"| Benefit-cost ratio | {('n/a' if m.bcr is not None else f'{m.bcr:.2f}')} |", f'| Average / weakest DSCR (repaying years) | {_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |', f"| Payback | {('beyond horizon' if m.payback_month is None else f'month {m.payback_month}')} |", '| Break-even meat price | ' + ('n/a' if m.break_even_meat_price_per_kg is None else f'₹{m.break_even_meat_price_per_kg:,.0f}/kg') + ' |', '', '## Annual operating summary', '', '| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |', '| ---: | ---: | ---: | ---: | ---: | ---: |']`
- **L788** `ifexp` swap branches — `29fad23f201b`
  - `lines += [f'## Viability ({horizon_years:g}-year projection)', '', '| Metric | Value |', '| --- | ---: |', f'| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |', f'| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |', f"| Benefit-cost ratio | {('n/a' if m.bcr is None else f'{m.bcr:.2f}')} |", f'| Average / weakest DSCR (repaying years) | {_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |', f"| Payback | {('beyond horizon' if m.payback_month is None else f'month {m.payback_month}')} |", '| Break-even meat price | ' + ('n/a' if m.break_even_meat_price_per_kg is None else f'₹{m.break_even_meat_price_per_kg:,.0f}/kg') + ' |', '', '## Annual operating summary', '', '| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |', '| ---: | ---: | ---: | ---: | ---: | ---: |']`
  - `lines += [f'## Viability ({horizon_years:g}-year projection)', '', '| Metric | Value |', '| --- | ---: |', f'| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |', f'| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |', f"| Benefit-cost ratio | {('n/a' if m.bcr is None else f'{m.bcr:.2f}')} |", f'| Average / weakest DSCR (repaying years) | {_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |', f"| Payback | {(f'month {m.payback_month}' if m.payback_month is None else 'beyond horizon')} |", '| Break-even meat price | ' + ('n/a' if m.break_even_meat_price_per_kg is None else f'₹{m.break_even_meat_price_per_kg:,.0f}/kg') + ' |', '', '## Annual operating summary', '', '| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |', '| ---: | ---: | ---: | ---: | ---: | ---: |']`
- **L788** `compare` Is -> IsNot — `edf77b6bc570`
  - `lines += [f'## Viability ({horizon_years:g}-year projection)', '', '| Metric | Value |', '| --- | ---: |', f'| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |', f'| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |', f"| Benefit-cost ratio | {('n/a' if m.bcr is None else f'{m.bcr:.2f}')} |", f'| Average / weakest DSCR (repaying years) | {_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |', f"| Payback | {('beyond horizon' if m.payback_month is None else f'month {m.payback_month}')} |", '| Break-even meat price | ' + ('n/a' if m.break_even_meat_price_per_kg is None else f'₹{m.break_even_meat_price_per_kg:,.0f}/kg') + ' |', '', '## Annual operating summary', '', '| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |', '| ---: | ---: | ---: | ---: | ---: | ---: |']`
  - `lines += [f'## Viability ({horizon_years:g}-year projection)', '', '| Metric | Value |', '| --- | ---: |', f'| NPV @ {assumptions.finance.discount_rate_annual:.0%} | {_cr(m.npv)} |', f'| IRR / MIRR | {_opt_pct(m.irr)} / {_opt_pct(m.mirr)} |', f"| Benefit-cost ratio | {('n/a' if m.bcr is None else f'{m.bcr:.2f}')} |", f'| Average / weakest DSCR (repaying years) | {_opt_dscr(m.avg_dscr)} / {_opt_dscr(m.min_dscr)} |', f"| Payback | {('beyond horizon' if m.payback_month is not None else f'month {m.payback_month}')} |", '| Break-even meat price | ' + ('n/a' if m.break_even_meat_price_per_kg is None else f'₹{m.break_even_meat_price_per_kg:,.0f}/kg') + ' |', '', '## Annual operating summary', '', '| Year | Revenue | Operating cost | EBITDA | Debt service | DSCR |', '| ---: | ---: | ---: | ---: | ---: | ---: |']`
- **L845** `boolop` Or -> And — `609b5dd2a76a`
  - `probabilities = plan_probabilities(assumptions, targets, purchases or None, runs=risk_runs)`
  - `probabilities = plan_probabilities(assumptions, targets, purchases and None, runs=risk_runs)`
