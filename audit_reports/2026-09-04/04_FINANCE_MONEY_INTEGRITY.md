# Red Team Audit #4 — "The Embezzler" — Financial Integrity, Pricing & Simulation Financials

**Date:** 2026-09-04 · **Method:** read-only adversarial code audit of backend money paths (`finance`, `milk`, `purchases`, `dashboard`, `simulation/*`) and frontend money UI. Every finding verified against source. Persona: an attacker corrupting financial records and price math.

## FINDINGS (ranked by financial impact)

### 1. Animals can be sold off-ledger: SOLD/CULLED without `sale_price` books zero revenue rows — HIGH
`backend/app/api/animals.py:1205-1223`, `backend/app/schemas/animals.py:233`

The status-change schema only enforces one direction — a price requires SOLD/CULLED — never that SOLD produces a ledger row:

```python
# schemas/animals.py:247-249
if self.new_status not in {"SOLD", "CULLED"} and (
    self.sale_price is not None or self.buyer_name is not None
):
    raise ValueError("Sale price and buyer require SOLD or CULLED status")
```

```python
# api/animals.py:1206-1223
animal.sale_price = money(payload.sale_price) if payload.sale_price is not None else None
...
if payload.sale_price is not None:          # ← the ONLY gate on the income row
    db.add(Transaction(... type=INCOME, category=ANIMAL_SALE, amount=money(payload.sale_price) ...))
```

Attack: a member with status-change permission sells 20 Osmanabadi bucks at ₹30,000 each (₹6,00,000 cash) and posts `{"new_status":"SOLD"}` with no `sale_price`. The herd count drops, the animal profile says SOLD — but `monthly_pnl`, `total_income` and every finance view show nothing. Compare the purchase side, where an explicit ₹0 still books a ₹0 row; here the money event vanishes entirely. The symmetric purchase gap also exists (`backend/app/services/purchases.py:202` — `if exact_total_price is not None:` books the expense; `schemas/purchases.py:32` allows `total_price=None` while still creating the animals), so inventory can be acquired cost-free on the books.

**Fix:** require a `sale_price` (0 allowed) for SOLD/CULLED, or emit a flagged ₹0 provenance row when omitted; same for purchase totals.

### 2. Milk-sold-vs-produced fence permits 10% fabricated milk income, cumulatively and permanently — MEDIUM
`backend/app/api/finance.py:623-646`

```python
if float(sold) + float(new_litres) > float(produced) * 1.10:
    raise HTTPException(422, ...)
```

`sold` is the all-time sum of non-voided MILK-income `milk_litres`; `produced` is all-time `MilkRecord.litres`. The 10% headroom is documented as "sale-side rounding and calf-milk reconciliation", but it is a hard revenue overbooking allowance that never resets: on a 200-Murrah dairy producing 4,00,000 L/yr priced at fat-based ~₹55/L effective, the fence accepts 40,000 unearned litres = **₹22,00,000/yr of fictitious income** from a finance.manage holder, fully coherent with the provenance validator (`amount = litres × fat% × ₹/kg fat ± ₹0.01`, `schemas/finance.py:135`). It also works retrospectively: inflate historical parlour readings (each within the 20 L/day species cap, `services/milk.py:81-96`) then book sales against them. *(Compounds with Audit #3 finding 2: fabricated parlour readings themselves are accepted outside the lactation window.)*

**Fix:** shrink the allowance (e.g. 0.5%) or make it litres-absolute rather than proportional, and window it per settlement period.

### 3. Fat-based pricing treats a litre as a kilogram (~3% systematic mispricing vs procurement slips) — LOW
`backend/app/schemas/finance.py:77-82`, `backend/app/simulation/engine.py:1453-1456`

```python
# schemas/finance.py:77-80 — paise-exact "expected" amount
return money(Decimal(str(litres)) * Decimal(str(fat_pct)) / 100 * Decimal(str(price_per_kg_fat)))
```
```python
# engine.py:1453-1454
effective_milk_price = sales.milk_price_per_kg_fat * sales.milk_fat_pct / 100.0
```

₹/kg-fat is a mass basis (plants pay on kg of milk × fat%), but both the ledger validator and the projection multiply the **volume** in litres directly by fat%. Murrah milk density is ~1.03 kg/L, so every fat-priced sale and every simulated dairy P&L under-states the slip value by ~3% (or, read the other way, the `milk_litres` field is silently kg). Ledger and simulation agree with each other, so there is no internal inconsistency to exploit — but reconciliations against plant statements will carry a persistent 3% gap.

**Fix:** add a density factor or require kg on fat-priced rows and document the convention.

### 4. Monte-Carlo seed is client-controlled and persisted — cherry-pickable lender-facing results — LOW
`backend/app/simulation/montecarlo.py:268`, `backend/app/simulation/assumptions.py:684-689`

```python
rng = random.Random(a.risk.seed)          # montecarlo.py:268
seed: int = Field(default=42, ge=0, le=2**31 - 1)   # assumptions.py:689
```

Reproducibility is a stated design goal, but with `runs` as low as 1 (`monte_carlo_runs: ge=1`) and a fully user-chosen 31-bit seed saved into `simulation_scenarios.assumptions`, any scenario-holder can re-roll seeds until `npv_p5`, `minimum_cash_p5` or `prob_liquidity_shortfall` cross a lender threshold, then save the flattering scenario — nothing marks a harvested seed. Draw order and RNG consumption are fixed (`_DRAW_ORDER`, common random numbers), so search is cheap and deterministic. *(Independently re-found by Audit #8 — cross-confirmed.)*

**Fix:** record the seed provenance/attempt count on saved scenarios, or default audits to a server-chosen seed.

### 5. Serialization boundary converts exact paise to float64 (bounded, quantified) — LOW
`backend/app/api/finance.py:587-588`, `backend/app/schemas/finance.py:176-177, 228-233`

```python
total_income=float(total_income), total_expense=float(total_expense)
```
Storage (`Numeric(14,2)`) and every SUM are exact Decimal; the only float conversion is at JSON time. float64 is paise-exact to ₹9,007,199,254,740.99 (2^53 paise); with the per-row ₹1e9 CHECK cap that is ~90 lakh max-value rows — unreachable. The frontend `formatMoney` (`frontend/src/lib/format.ts:4-25`) degrades gracefully at 1e21 and renders non-finite as "—". No exploitation path found; flagged only because `total_income` is a float type in the contract.

## MONEY PATHS VERIFIED SAFE

1. **Milk sale pricing → ledger → P&L is drift-free.** `money()` (`utils.py:13-19`) quantizes via `Decimal(str(value))` ROUND_HALF_UP (no binary-float contamination); the provenance validator reproduces the amount within one paisa; amounts stored `Numeric(14,2)`; `monthly_pnl`/totals aggregate with SQL `SUM` over void-excluded rows in exact Decimal, with correct half-open month bounds (`date >= first, date < following_month`) — no 0.1+0.2 accumulation across thousands of records (rounding happens once per row, ≤₹0.01, tolerance-enforced at `schemas/finance.py:135`).
2. **No double-counting / dedup enforced structurally.** `uq_milk_records_animal_day_shift` + in-place correction with frozen `original_*` audit columns (`models/milk.py:40`, `services/milk.py:99-116`); finance void-and-replace keeps exactly one active row per source via partial unique index `uq_transactions_active_source` (`models/finance.py:97-106`); voided rows excluded from all totals; the milk-ledger advisory lock serializes the sold-vs-produced check.
3. **Purchases/ledger immutable after settlement.** No edit or delete endpoints exist for `PurchaseBatch` or `Transaction`; corrections are audited void+replacement; a PURCHASE_BATCH with allocated animals refuses amount/date changes (`api/finance.py:470-478`); feed-purchase corrections re-derive inventory price/qty under row locks with negative-stock rejection (`api/finance.py:159-302`); per-head allocation uses paise-exact `allocate_money` (`utils.py:22-41`).
4. **Simulation math hardened against NaN/Inf/div-zero and never touches the real ledger.** All assumption floats `allow_inf_nan=False` with magnitude caps (`assumptions.py:17-46`); the lactation curve raises loudly on degenerate inputs instead of dividing by zero (`lactation.py:95-99`); EMI uses expm1/log1p down to the r→0 limit (`simulation/finance.py:29-46`); BCR/IRR/MIRR return `None` rather than inf; the histogram pads degenerate ranges; scenarios persist only assumptions — results are always recomputed (`models/simulation.py`, `api/simulation.py`).
5. **No JOIN fan-out or int32 overflow in money aggregation.** Finance/milk/purchase aggregates group before summing, use `count(distinct …)` for animal/day counts, `COALESCE` only with correctly-typed zeros, and money never passes through an int-typed field; TS-visible integers (seed, ids) explicitly capped below 2^53/2^31 (`assumptions.py:684-689`).

## VERDICT

Financial integrity is **strong — materially above typical SaaS baselines**: exact-Decimal storage and aggregation, paise-exact allocation, an immutable audited ledger, structural double-count prevention, and unusually well-defended simulation numerics. No arithmetic corruption, rounding-drift, float-poisoning, or aggregation-inflation bug could be executed. The exploitable surface is **semantic, not arithmetic**: a privileged insider can move assets (sales, batch purchases) with the price deliberately omitted so the ledger never sees the money (Finding 1), and can overbook milk revenue by a hard-coded 10% of production (Finding 2). Both are policy gaps a fraudster needs no math bug for — close them and this ledger is bank-grade.
