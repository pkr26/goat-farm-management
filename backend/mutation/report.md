# Backend mutation testing report

- mutants in manifest: **6565**
- executed: **6565** (killed 6043, timeout 5, survived 74, not-covered 443, errors 0)
- **mutation score: 98.8%** (killed / executed-with-coverage)

## Surviving mutants

### `app/api/_run_limits.py` (2 survivors)

- **L282** `boolconst` False -> True — `48598c5bfacc`
  - `self._prune_bucket(candidate, touch=False)`
  - `self._prune_bucket(candidate, touch=True)`
- **L363** `boolconst` True -> False — `2ea2b4a4ed7a`
  - `released = True`
  - `released = False`

### `app/api/_shared.py` (2 survivors)

- **L420** `boolconst` True -> False — `08230660638f`
  - `assignee_membership_statement = assignee_membership_statement.with_for_update(read=True)`
  - `assignee_membership_statement = assignee_membership_statement.with_for_update(read=False)`
- **L424** `boolconst` True -> False — `40c77220ddf9`
  - `assignee_user_statement = assignee_user_statement.with_for_update(read=True)`
  - `assignee_user_statement = assignee_user_statement.with_for_update(read=False)`

### `app/api/auth.py` (4 survivors)

- **L1213** `intconst` n -> n-1 — `283d855f0cdb`
  - `user_id = row[1].id`
  - `user_id = row[0].id`
- **L1228** `boolconst` True -> False — `99d9648e40b3`
  - `pin_work_started = True`
  - `pin_work_started = False`
- **L1942** `intconst` n -> n-1 — `19aeaa3b634e`
  - `locked_user.token_version += 1`
  - `locked_user.token_version += 0`
- **L2752** `ifexp` swap branches — `0b74633df6ec`
  - `user_id = user.id if user is not None else challenge_user_id`
  - `user_id = challenge_user_id if user is not None else user.id`

### `app/deps.py` (2 survivors)

- **L37** `boolconst` False -> True — `3cb2837ae301`
  - `_bearer_scheme = HTTPBearer(auto_error=False)`
  - `_bearer_scheme = HTTPBearer(auto_error=True)`
- **L275** `intconst` n -> n+1 — `2afcc78f0eeb`
  - `async def purge_expired_refresh_sessions(db: AsyncSession, older_than_days: int=30, *, batch_size: int) -> int:
    """Delete one finite, lock-skipping batch of retained expired sessions."""
    from datetime import timedelta
    if not 1 <= batch_size <= 10000:
        raise ValueError('batch_size must be between 1 and 10000')
    cutoff = utcnow() - timedelta(days=older_than_days)
    candidate_ids = list((await db.execute(select(RefreshSession.id).where(RefreshSession.expires_at < cutoff).order_by(RefreshSession.expires_at, RefreshSession.id).limit(batch_size).with_for_update(skip_locked=True))).scalars())
    if not candidate_ids:
        return 0
    removed = await db.execute(delete(RefreshSession).where(RefreshSession.id.in_(candidate_ids)).returning(RefreshSession.id))
    return len(removed.scalars().all())`
  - `async def purge_expired_refresh_sessions(db: AsyncSession, older_than_days: int=31, *, batch_size: int) -> int:
    """Delete one finite, lock-skipping batch of retained expired sessions."""
    from datetime import timedelta
    if not 1 <= batch_size <= 10000:
        raise ValueError('batch_size must be between 1 and 10000')
    cutoff = utcnow() - timedelta(days=older_than_days)
    candidate_ids = list((await db.execute(select(RefreshSession.id).where(RefreshSession.expires_at < cutoff).order_by(RefreshSession.expires_at, RefreshSession.id).limit(batch_size).with_for_update(skip_locked=True))).scalars())
    if not candidate_ids:
        return 0
    removed = await db.execute(delete(RefreshSession).where(RefreshSession.id.in_(candidate_ids)).returning(RefreshSession.id))
    return len(removed.scalars().all())`

### `app/ratelimit.py` (3 survivors)

- **L149** `boolconst` True -> False — `361c9338ccbe`
  - `self._reclassify(bucket, touch=True)`
  - `self._reclassify(bucket, touch=False)`
- **L298** `boolconst` True -> False — `336ac58ab6b0`
  - `self._reclassify(bucket, touch=True)`
  - `self._reclassify(bucket, touch=False)`
- **L483** `intconst` n -> n+1 — `835ed9e0e023`
  - `_throttle_rejections[scope] += 1`
  - `_throttle_rejections[scope] += 2`

### `app/security.py` (10 survivors)

- **L363** `binop` BitOr -> BitAnd — `5b155eaac29a`
  - `fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)`
  - `fd = os.open(path, os.O_RDONLY & os.O_CLOEXEC | os.O_NONBLOCK)`
- **L363** `binop` BitOr -> BitAnd — `734d69dbd08a`
  - `fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)`
  - `fd = os.open(path, (os.O_RDONLY | os.O_CLOEXEC) & os.O_NONBLOCK)`
- **L372** `binop` Add -> Sub — `2193e9062dc9`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES - 1 - len(raw)))`
- **L372** `intconst` n -> n+1 — `38451df940d7`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(65 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
- **L372** `intconst` n -> n-1 — `5941e927d258`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 0 - len(raw)))`
- **L372** `intconst` n -> n+1 — `a0a4c9e644ce`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1025, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
- **L372** `intconst` n -> n+1 — `a7a0bf2d67ae`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 2 - len(raw)))`
- **L372** `intconst` n -> n-1 — `a9a9a112e444`
  - `chunk = os.read(fd, min(64 * 1024, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
  - `chunk = os.read(fd, min(64 * 1023, _MAX_JWT_KEY_BYTES + 1 - len(raw)))`
- **L433** `intconst` n -> n+1 — `04d5b99fb14d`
  - `key = rsa.generate_private_key(public_exponent=65537, key_size=2048)`
  - `key = rsa.generate_private_key(public_exponent=65537, key_size=2049)`
- **L506** `intconst` n -> n+1 — `1d28134c8738`
  - `private_mode = (existing_mode | 448) & ~63`
  - `private_mode = (existing_mode | 449) & ~63`

### `app/services/cadence.py` (2 survivors)

- **L408** `boolconst` False -> True — `25247dade37f`
  - `created = False`
  - `created = True`
- **L429** `loopjump` continue -> break — `30fd7a5739d7`
  - `continue`
  - `break`

### `app/services/chronology.py` (2 survivors)

- **L170** `compare` Eq -> NotEq — `260cdc6fdde8`
  - `first_kidding = select(func.min(KiddingRecord.date)).where(KiddingRecord.farm_id == animal.farm_id, KiddingRecord.doe_id == animal.id).scalar_subquery()`
  - `first_kidding = select(func.min(KiddingRecord.date)).where(KiddingRecord.farm_id != animal.farm_id, KiddingRecord.doe_id == animal.id).scalar_subquery()`
- **L171** `compare` Eq -> NotEq — `290e1d705e12`
  - `first_kidding = select(func.min(KiddingRecord.date)).where(KiddingRecord.farm_id == animal.farm_id, KiddingRecord.doe_id == animal.id).scalar_subquery()`
  - `first_kidding = select(func.min(KiddingRecord.date)).where(KiddingRecord.farm_id == animal.farm_id, KiddingRecord.doe_id != animal.id).scalar_subquery()`

### `app/services/dashboard.py` (2 survivors)

- **L248** `compare` LtE -> Lt — `1bd86a63d0b9`
  - `breeding_rules = [and_(context.c.sex == 'F', context.c.current_bucket.in_([Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value]), context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.sex == 'F', context.c.current_bucket == Bucket.RESTING.value, resting_ready, context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.current_bucket == Bucket.PREGNANCY_EARLY.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=pregnancy_late_day)), and_(context.c.current_bucket == Bucket.PREGNANCY_LATE.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=due_window_day))]`
  - `breeding_rules = [and_(context.c.sex == 'F', context.c.current_bucket.in_([Bucket.FOUNDATION.value, Bucket.FEMALE_KIDS.value]), context.c.effective_dob.is_not(None), context.c.effective_dob < age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.sex == 'F', context.c.current_bucket == Bucket.RESTING.value, resting_ready, context.c.effective_dob.is_not(None), context.c.effective_dob <= age_cutoff, context.c.latest_weight_as_of >= breeding_weight, context.c.open_pregnancy_date.is_(None)), and_(context.c.current_bucket == Bucket.PREGNANCY_EARLY.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=pregnancy_late_day)), and_(context.c.current_bucket == Bucket.PREGNANCY_LATE.value, context.c.open_pregnancy_date <= reference_date - timedelta(days=due_window_day))]`
- **L277** `compare` LtE -> Lt — `01b6ea9a52c2`
  - `market_rule = and_(context.c.sex == 'M', context.c.current_bucket == Bucket.MALE_KIDS.value, context.c.effective_dob.is_not(None), context.c.effective_dob <= add_months(reference_date, -MEAT_SALE_AGE_MONTHS[0]), context.c.latest_weight_as_of >= MEAT_SALE_WEIGHT_KG[0], context.c.has_active_withdrawal.is_(False))`
  - `market_rule = and_(context.c.sex == 'M', context.c.current_bucket == Bucket.MALE_KIDS.value, context.c.effective_dob.is_not(None), context.c.effective_dob < add_months(reference_date, -MEAT_SALE_AGE_MONTHS[0]), context.c.latest_weight_as_of >= MEAT_SALE_WEIGHT_KG[0], context.c.has_active_withdrawal.is_(False))`

### `app/services/feeding.py` (5 survivors)

- **L138** `intconst` n -> n+1 — `123b0575f23f`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 100), item[0]))`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 101), item[0]))`
- **L138** `intconst` n -> n+1 — `c33b03cc4105`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 100), item[0]))`
  - `ranked = sorted(enumerate(weighted), key=lambda item: (-(item[1][1] % 100), item[1]))`
- **L280** `compare` LtE -> Lt — `84f48a785104`
  - `is_dependent_kid = and_(Animal.dam_id.is_not(None), age_days <= weaning_days, exists(select(1).where(dam_animal.id == Animal.dam_id, dam_animal.farm_id == farm.id, dam_animal.status == AnimalStatus.ACTIVE.value, dam_animal.current_bucket == Bucket.RECOVERY.value)))`
  - `is_dependent_kid = and_(Animal.dam_id.is_not(None), age_days < weaning_days, exists(select(1).where(dam_animal.id == Animal.dam_id, dam_animal.farm_id == farm.id, dam_animal.status == AnimalStatus.ACTIVE.value, dam_animal.current_bucket == Bucket.RECOVERY.value)))`
- **L564** `intconst` n -> n+1 — `0272d7b3f8a7`
  - `on_hand = Decimal(str(item.qty_on_hand)) if item else Decimal(0)`
  - `on_hand = Decimal(str(item.qty_on_hand)) if item else Decimal(1)`
- **L717** `intconst` n -> n+1 — `2693170ea2ea`
  - `available = Decimal(str(stock.qty_on_hand)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP) if stock else Decimal(0)`
  - `available = Decimal(str(stock.qty_on_hand)).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP) if stock else Decimal(1)`

### `app/services/health.py` (3 survivors)

- **L84** `intconst` n -> n+1 — `0568e9b5a08e`
  - `phrase = phrase[closing + 1:]`
  - `phrase = phrase[closing + 2:]`
- **L180** `intconst` n -> n-1 — `8eebcd1901a8`
  - `aliases.append(abbrev_match.group(1))`
  - `aliases.append(abbrev_match.group(0))`
- **L580** `intconst` n -> n+1 — `68435aa347bf`
  - `latest = select(literal(template.id).label('template_id'), HealthEvent.id.label('event_id'), HealthEvent.date.label('event_date'), HealthEvent.next_due_date, HealthEvent.next_due_authority, literal(False).label('is_primary')).where(HealthEvent.animal_id == animal.id, HealthEvent.schedule_template_id == template.id, HealthEvent.type.in_([HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value])).order_by(HealthEvent.date.desc(), HealthEvent.id.desc()).limit(2).subquery()`
  - `latest = select(literal(template.id).label('template_id'), HealthEvent.id.label('event_id'), HealthEvent.date.label('event_date'), HealthEvent.next_due_date, HealthEvent.next_due_authority, literal(False).label('is_primary')).where(HealthEvent.animal_id == animal.id, HealthEvent.schedule_template_id == template.id, HealthEvent.type.in_([HealthEventType.VACCINE.value, HealthEventType.DEWORMING.value])).order_by(HealthEvent.date.desc(), HealthEvent.id.desc()).limit(3).subquery()`

### `app/services/kidding.py` (1 survivors)

- **L270** `ifexp` swap branches — `8473390c26ea`
  - `animal = Animal(farm_id=farm.id, tag_number=tag, sex=kid['sex'], breed=doe.breed, source=AnimalSource.BORN.value, date_of_birth=kidding_date, birth_type=birth_type.value if birth_type else None, dam_id=doe.id, sire_id=br.buck_id, birth_weight=kid['birth_weight'], current_bucket=born_bucket, status=AnimalStatus.ACTIVE.value if kid['status'] == KidStatus.ALIVE.value else AnimalStatus.DEAD.value, status_date=kid['mortality_reported_at'] if kid['status'] == KidStatus.DIED.value else None, status_notes='Neonatal mortality recorded' if kid['status'] == KidStatus.DIED.value else None, mortality_reported_at=kid['mortality_reported_at'])`
  - `animal = Animal(farm_id=farm.id, tag_number=tag, sex=kid['sex'], breed=doe.breed, source=AnimalSource.BORN.value, date_of_birth=kidding_date, birth_type=birth_type.value if birth_type else None, dam_id=doe.id, sire_id=br.buck_id, birth_weight=kid['birth_weight'], current_bucket=born_bucket, status=AnimalStatus.ACTIVE.value if kid['status'] == KidStatus.ALIVE.value else AnimalStatus.DEAD.value, status_date=kid['mortality_reported_at'] if kid['status'] == KidStatus.DIED.value else None, status_notes=None if kid['status'] == KidStatus.DIED.value else 'Neonatal mortality recorded', mortality_reported_at=kid['mortality_reported_at'])`

### `app/services/notifications/service.py` (1 survivors)

- **L148** `intconst` n -> n-1 — `86a0beb94f88`
  - `attempts = max(1, settings.notifications_send_retry_attempts)`
  - `attempts = max(0, settings.notifications_send_retry_attempts)`

### `app/services/purchases.py` (1 survivors)

- **L218** `boolconst` True -> False — `459b822c15ac`
  - `db.add_all([WeightRecord(animal_id=animal.id, date=batch_date, weight_kg=weight, notes='Arrival weight (individual)', created_by_id=created_by_id) for animal, weight in zip(animals, individual_weights_kg, strict=True)])`
  - `db.add_all([WeightRecord(animal_id=animal.id, date=batch_date, weight_kg=weight, notes='Arrival weight (individual)', created_by_id=created_by_id) for animal, weight in zip(animals, individual_weights_kg, strict=False)])`

### `app/services/retention.py` (1 survivors)

- **L221** `compare` Eq -> NotEq — `0a19957005dd`
  - `old_crop_ids = select(ScreeningCrop.id).where(ScreeningCrop.farm_id == farm_id, ScreeningCrop.image_id.in_(old_image_ids))`
  - `old_crop_ids = select(ScreeningCrop.id).where(ScreeningCrop.farm_id != farm_id, ScreeningCrop.image_id.in_(old_image_ids))`

### `app/services/screening/detect.py` (1 survivors)

- **L103** `loopjump` continue -> break — `4ad3ecd9338b`
  - `continue`
  - `break`

### `app/services/screening/images.py` (1 survivors)

- **L159** `intconst` n -> n+1 — `0e0c282840d5`
  - `top = max(0, round(box.y / 1000 * height - margin_y))`
  - `top = max(1, round(box.y / 1000 * height - margin_y))`

### `app/services/screening/pipeline.py` (3 survivors)

- **L695** `boolconst` True -> False — `4749c4e1c3a8`
  - `identities_expired = True`
  - `identities_expired = False`
- **L706** `boolop` Or -> And — `3df37e83009a`
  - `image.error = f"{image.error or 'screening failed'}; terminal after {attempts} attempts"`
  - `image.error = f"{image.error and 'screening failed'}; terminal after {attempts} attempts"`
- **L719** `boolconst` True -> False — `3a69c2d4f26f`
  - `identities_expired = True`
  - `identities_expired = False`

### `app/services/screening/rotation.py` (1 survivors)

- **L80** `binop` Add -> Sub — `3fc9bd265609`
  - `candidate = self._providers[(on.toordinal() + step) % len(self._providers)]`
  - `candidate = self._providers[(on.toordinal() - step) % len(self._providers)]`

### `app/services/screening/s3.py` (1 survivors)

- **L248** `intconst` n -> n+1 — `328ae4fd39ba`
  - `content_length = int(response.get('ContentLength', 0))`
  - `content_length = int(response.get('ContentLength', 1))`

### `app/services/simulation_calibration.py` (22 survivors)

- **L172** `compare` Lt -> LtE — `7f7ad19ed247`
  - `lower = max((a for a in ages if a < age))`
  - `lower = max((a for a in ages if a <= age))`
- **L199** `boolconst` True -> False — `51a3ddbe7334`
  - `assumptions = get_preset(breed, system).model_copy(deep=True)`
  - `assumptions = get_preset(breed, system).model_copy(deep=False)`
- **L351** `intconst` n -> n-1 — `8273768d68a7`
  - `record(f'herd.{target_key}', count_previous, count_calibrated, current_head, 'Exact ACTIVE-animal cohort count on the reference date', 'animals', medium=1, high=1)`
  - `record(f'herd.{target_key}', count_previous, count_calibrated, current_head, 'Exact ACTIVE-animal cohort count on the reference date', 'animals', medium=0, high=1)`
- **L392** `boolop` Or -> And — `728c3f78bcbe`
  - `dob = weight_row.date_of_birth or weight_row.estimated_dob`
  - `dob = weight_row.date_of_birth and weight_row.estimated_dob`
- **L556** `intconst` n -> n-1 — `0d82b7be1621`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 220]`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 219]`
- **L556** `compare` LtE -> Lt — `4b8a09f6fd88`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 220]`
  - `valid_gestations = [days for days in gestation_days if 90 < days <= 220]`
- **L556** `intconst` n -> n-1 — `daa60c97f16a`
  - `valid_gestations = [days for days in gestation_days if 90 <= days <= 220]`
  - `valid_gestations = [days for days in gestation_days if 89 <= days <= 220]`
- **L559** `intconst` n -> n-1 — `79c0d9c792a3`
  - `calibrated_gestation = min(7, max(1, round(median(valid_gestations) / 30.44)))`
  - `calibrated_gestation = min(6, max(1, round(median(valid_gestations) / 30.44)))`
- **L559** `intconst` n -> n+1 — `bc8c9a5e4ae8`
  - `calibrated_gestation = min(7, max(1, round(median(valid_gestations) / 30.44)))`
  - `calibrated_gestation = min(7, max(2, round(median(valid_gestations) / 30.44)))`
- **L559** `intconst` n -> n-1 — `e22198616a9c`
  - `calibrated_gestation = min(7, max(1, round(median(valid_gestations) / 30.44)))`
  - `calibrated_gestation = min(7, max(0, round(median(valid_gestations) / 30.44)))`
- **L620** `compare` Eq -> NotEq — `74d2ed5df2ce`
  - `female_alive = sum((kid_row.sex == 'F' for kid_row in alive_rows))`
  - `female_alive = sum((kid_row.sex != 'F' for kid_row in alive_rows))`
- **L625** `binop` Div -> Mult — `778d6844d1fb`
  - `observed_stillbirth = stillborn / kid_count`
  - `observed_stillbirth = stillborn * kid_count`
- **L661** `compare` LtE -> Lt — `779d24f8c62a`
  - `died = sum((kid_row.status == 'DIED' and kid_row.mortality_reported_at is not None and (kid_row.mortality_reported_at <= add_months(kid_row.date, 3)) for kid_row in weaned_rows))`
  - `died = sum((kid_row.status == 'DIED' and kid_row.mortality_reported_at is not None and (kid_row.mortality_reported_at < add_months(kid_row.date, 3)) for kid_row in weaned_rows))`
- **L716** `boolop` Or -> And — `2e2f8da80dda`
  - `exposure_start = max(period_start, mortality_row.purchase_date or period_start)`
  - `exposure_start = max(period_start, mortality_row.purchase_date and period_start)`
- **L723** `loopjump` continue -> break — `63a62dfd46b6`
  - `continue`
  - `break`
- **L988** `intconst` n -> n-1 — `79e4e2a73e6e`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((0 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
- **L990** `compare` Eq -> NotEq — `273cb9130ec7`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category != feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
- **L990** `boolop` And -> Or — `6ab4a8756b55`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category or transaction_row.feed_quantity_kg is not None or transaction_row.feed_unit_price_per_kg is not None))`
- **L991** `compare` IsNot -> Is — `659467612979`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is not None and (transaction_row.feed_unit_price_per_kg is not None)))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.feed_category == feed_category and transaction_row.feed_quantity_kg is None and (transaction_row.feed_unit_price_per_kg is not None)))`
- **L1051** `intconst` n -> n+1 — `152f3b5de3a1`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((2 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
- **L1069** `compare` Eq -> NotEq — `61dd43f01fbe`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category != 'LABOUR'))`
- **L1069** `intconst` n -> n-1 — `7be0e1b67d1a`
  - `sample_size = sum((1 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`
  - `sample_size = sum((0 for transaction_row in transaction_rows if transaction_row.category == 'LABOUR'))`

### `app/services/tasks.py` (4 survivors)

- **L207** `compare` IsNot -> Is — `83b7b52fd9e9`
  - `animal_ids = [kid.animal_id for kid in kids if kid.animal_id is not None]`
  - `animal_ids = [kid.animal_id for kid in kids if kid.animal_id is None]`
- **L384** `boolconst` False -> True — `0f1fd0c006b5`
  - `weaning_doe_can_rest = False`
  - `weaning_doe_can_rest = True`
- **L531** `intconst` n -> n+1 — `42884e4f32e3`
  - `candidates.insert(0, weaning_doe)`
  - `candidates.insert(1, weaning_doe)`
- **L574** `ifexp` swap branches — `a2578300b76d`
  - `move_animal(db, postpartum_doe, Bucket.RESTING.value, 'Postpartum recovery complete; no surviving kids', created_by_id=user.id if user else None, context='postpartum', reference_date=resolved_movement_date)`
  - `move_animal(db, postpartum_doe, Bucket.RESTING.value, 'Postpartum recovery complete; no surviving kids', created_by_id=None if user else user.id, context='postpartum', reference_date=resolved_movement_date)`
