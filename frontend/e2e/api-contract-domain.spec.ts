import {
  expect,
  test,
  type APIRequestContext,
  type APIResponse,
} from "@playwright/test";

import type {
  AnimalCreateIn,
  AnimalOut,
  AnimalProfileOut,
  BreedingCreateIn,
  BreedingRecordOut,
  FarmOut,
  FeedInventoryOut,
  FeedingPlanOut,
  FeedRecipeOut,
  FeedSettingIn,
  FinanceOut,
  FinishedFeedStockOut,
  HealthBulkTargetPreviewOut,
  HealthEventIn,
  HealthEventMutationOut,
  HealthPurchaseBatchOptionListOut,
  HerdSnapshotOut,
  MovementRestrictionHistoryOut,
  PregnancyLossIn,
  RecipeListOut,
  ScenarioCompareOut,
  ScenarioCreateIn,
  ScenarioListOut,
  ScenarioOut,
  ScenarioUpdateIn,
  SimulationAssumptions,
  StatusChangeIn,
  StockAddIn,
  TaskCreateIn,
  TaskOut,
  TaskSkipIn,
  TokenOut,
  TransactionCorrectionIn,
  TransactionIn,
  TransactionOut,
  UltrasoundIn,
} from "../src/api/generated/models";

const PROXY_ORIGIN = "http://localhost:3000";
const FARM_TIMEZONE = "America/Phoenix";
const RUN_SUFFIX = `${Date.now().toString(36)}-${Math.floor(Math.random() * 46_656).toString(36)}`;

let sequence = 0;
let farmHeaders: Record<string, string>;
let farmId: number;
let ownerId: number;

function uniqueValue(prefix: string): string {
  sequence += 1;
  return `${prefix}-${RUN_SUFFIX}-${sequence}`;
}

function farmDate({ days = 0, months = 0 }: { days?: number; months?: number } = {}): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: FARM_TIMEZONE,
      year: "numeric",
      month: "numeric",
      day: "numeric",
    })
      .formatToParts(new Date())
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)]),
  ) as Record<"year" | "month" | "day", number>;
  const shifted = new Date(
    Date.UTC(parts.year, parts.month - 1 + months, parts.day + days, 12),
  );
  return shifted.toISOString().slice(0, 10);
}

async function assertProxyResponse(response: APIResponse, status: number): Promise<void> {
  expect(new URL(response.url()).origin).toBe(PROXY_ORIGIN);
  if (response.status() !== status) {
    throw new Error(
      `${response.url()} returned ${response.status()} ` +
        `(expected ${status}): ${await response.text()}`,
    );
  }
}

async function jsonResponse<T>(response: APIResponse, status: number): Promise<T> {
  await assertProxyResponse(response, status);
  expect(response.headers()["content-type"]).toContain("application/json");
  return (await response.json()) as T;
}

async function emptyResponse(response: APIResponse, status: number): Promise<void> {
  await assertProxyResponse(response, status);
  expect((await response.body()).byteLength).toBe(0);
}

async function createHistoricalAnimal(
  request: APIRequestContext,
  overrides: Partial<AnimalCreateIn> = {},
): Promise<AnimalOut> {
  const payload = {
    tag_number: uniqueValue("CTR"),
    sex: "F",
    source: "BORN",
    current_bucket: "FOUNDATION",
    historical_import_reason: "Real-stack API contract fixture",
    ...overrides,
  } satisfies AnimalCreateIn;
  return jsonResponse<AnimalOut>(
    await request.post("/api/animals", { data: payload, headers: farmHeaders }),
    201,
  );
}

test.describe.serial("frontend proxy domain API contracts", () => {
  test.beforeAll(async ({ request }) => {
    const email = `contract-${RUN_SUFFIX}@goatfarm.test`;
    const register = await jsonResponse<TokenOut>(
      await request.post("/api/auth/register", {
        data: {
          email,
          password: "contract-pass-1234",
          name: "Contract Owner",
        },
      }),
      201,
    );
    expect(register).toMatchObject({
      token_type: "bearer",
      user: { email, name: "Contract Owner" },
    });
    expect(register.access_token).not.toHaveLength(0);
    ownerId = register.user.id;

    const authHeaders = { Authorization: `Bearer ${register.access_token}` };
    const farmName = `Contract Farm ${RUN_SUFFIX}`;
    const farm = await jsonResponse<FarmOut>(
      await request.post("/api/auth/farms", {
        data: { name: farmName, location: "Proxy contract", timezone: FARM_TIMEZONE },
        headers: { ...authHeaders, "Idempotency-Key": crypto.randomUUID() },
      }),
      201,
    );
    expect(farm).toEqual({
      id: expect.any(Number),
      name: farmName,
      location: "Proxy contract",
      timezone: FARM_TIMEZONE,
      role: null,
    });
    farmId = farm.id;
    farmHeaders = { ...authHeaders, "X-Farm-Id": String(farm.id) };
  });

  test("animal status mutation returns and persists the requested lifecycle facts", async ({
    request,
  }) => {
    const animal = await createHistoricalAnimal(request, {
      tag_number: uniqueValue("STATUS"),
      sex: "M",
    });
    const statusDate = farmDate();
    const payload = {
      new_status: "SOLD",
      date: statusDate,
      sale_price: 425.5,
      buyer_name: "Contract buyer",
      notes: "Verified through the Next rewrite",
    } satisfies StatusChangeIn;

    const changed = await jsonResponse<AnimalOut>(
      await request.post(`/api/animals/${animal.id}/status`, {
        data: payload,
        headers: farmHeaders,
      }),
      200,
    );
    expect(changed).toMatchObject({
      id: animal.id,
      tag_number: animal.tag_number,
      status: "SOLD",
      status_date: statusDate,
      sale_price: 425.5,
      current_bucket: "FOUNDATION",
    });

    const profile = await jsonResponse<AnimalProfileOut>(
      await request.get(`/api/animals/${animal.id}`, { headers: farmHeaders }),
      200,
    );
    expect(profile.animal).toMatchObject({
      id: animal.id,
      tag_number: animal.tag_number,
      status: "SOLD",
      status_date: statusDate,
      sale_price: 425.5,
    });
  });

  test("breeding GET, live-pregnancy GET, and pregnancy abort agree on one record", async ({
    request,
  }) => {
    const breedingDate = farmDate({ days: -60 });
    const today = farmDate();
    const adultDob = farmDate({ months: -24 });
    const doe = await createHistoricalAnimal(request, {
      tag_number: uniqueValue("DOE"),
      sex: "F",
      current_bucket: "BREEDING",
      date_of_birth: adultDob,
      weight_kg: 35,
      weight_date: breedingDate,
    });
    const buck = await createHistoricalAnimal(request, {
      tag_number: uniqueValue("BUCK"),
      sex: "M",
      current_bucket: "BREEDING",
      date_of_birth: adultDob,
      weight_kg: 40,
      weight_date: breedingDate,
    });
    const breedingPayload = {
      doe_id: doe.id,
      buck_id: buck.id,
      breeding_date: breedingDate,
      heat_cycle_number: 1,
    } satisfies BreedingCreateIn;
    const created = await jsonResponse<BreedingRecordOut>(
      await request.post("/api/breeding", {
        data: breedingPayload,
        headers: farmHeaders,
      }),
      201,
    );
    expect(created).toMatchObject({
      doe_id: doe.id,
      buck_id: buck.id,
      doe_tag: doe.tag_number,
      buck_tag: buck.tag_number,
      breeding_date: breedingDate,
      method: "NATURAL",
      heat_cycle_number: 1,
      ultrasound_done: false,
      pregnant: null,
      outcome: "PENDING",
      has_kidding: false,
    });

    const initialRead = await jsonResponse<BreedingRecordOut>(
      await request.get(`/api/breeding/${created.id}`, { headers: farmHeaders }),
      200,
    );
    expect(initialRead).toEqual(created);

    const ultrasoundPayload = {
      pregnant: true,
      date: today,
      kid_count: 2,
    } satisfies UltrasoundIn;
    const confirmed = await jsonResponse<BreedingRecordOut>(
      await request.post(`/api/breeding/${created.id}/ultrasound`, {
        data: ultrasoundPayload,
        headers: farmHeaders,
      }),
      200,
    );
    expect(confirmed).toMatchObject({
      id: created.id,
      ultrasound_done: true,
      ultrasound_result_date: today,
      pregnant: true,
      kid_count_detected: 2,
      expected_kidding_date: expect.any(String),
      outcome: "CONFIRMED_PREGNANT",
      has_kidding: false,
    });

    const livePregnancy = await jsonResponse<BreedingRecordOut>(
      await request.get(`/api/kidding/pregnancies/${created.id}`, {
        headers: farmHeaders,
      }),
      200,
    );
    expect(livePregnancy).toEqual(confirmed);

    const lossPayload = {
      loss_date: today,
      cause: "OTHER",
      notes: "Contract-test observed pregnancy loss",
    } satisfies PregnancyLossIn;
    const aborted = await jsonResponse<BreedingRecordOut>(
      await request.post(`/api/breeding/${created.id}/abort`, {
        data: lossPayload,
        headers: farmHeaders,
      }),
      200,
    );
    expect(aborted).toMatchObject({
      id: created.id,
      pregnant: false,
      outcome: "ABORTED",
      loss_date: today,
      loss_cause: "OTHER",
      loss_notes: lossPayload.notes,
      loss_recorded_by_id: ownerId,
      loss_recorded_at: expect.any(String),
      has_kidding: false,
    });

    const persisted = await jsonResponse<BreedingRecordOut>(
      await request.get(`/api/breeding/${created.id}`, { headers: farmHeaders }),
      200,
    );
    expect(persisted).toEqual(aborted);
    const noLongerLive = await request.get(`/api/kidding/pregnancies/${created.id}`, {
      headers: farmHeaders,
    });
    const notFound = await jsonResponse<{ detail: string }>(noLongerLive, 404);
    expect(notFound.detail).toBe("Breeding record not found");
  });

  test("health batch lookup/preview and restriction clearance round-trip their data", async ({
    request,
  }) => {
    const today = farmDate();
    const purchasedPayload = {
      tag_number: uniqueValue("BATCH"),
      sex: "F",
      source: "PURCHASED",
      current_bucket: "QUARANTINE",
      purchase_date: today,
      purchase_price: 2_500,
      seller_name: "Contract seller",
      weight_kg: 24,
      weight_date: today,
    } satisfies AnimalCreateIn;
    const purchased = await jsonResponse<AnimalOut>(
      await request.post("/api/animals", {
        data: purchasedPayload,
        // Source PURCHASED books money, so the backend requires the key (RT-C-4).
        headers: { ...farmHeaders, "Idempotency-Key": crypto.randomUUID() },
      }),
      201,
    );
    expect(purchased).toMatchObject({
      tag_number: purchasedPayload.tag_number,
      source: "PURCHASED",
      current_bucket: "QUARANTINE",
      status: "ACTIVE",
      purchase_date: today,
      purchase_price: 2_500,
    });

    const batches = await jsonResponse<HealthPurchaseBatchOptionListOut>(
      await request.get("/api/health/purchase-batches", {
        headers: farmHeaders,
        params: { limit: 10, offset: 0 },
      }),
      200,
    );
    expect(batches).toMatchObject({ total: 1, limit: 10, offset: 0 });
    expect(batches.batches).toHaveLength(1);
    expect(batches.batches[0]).toEqual({
      id: expect.any(Number),
      active_quarantine_animal_count: 1,
    });
    const batchId = batches.batches[0].id;

    const preview = await jsonResponse<HealthBulkTargetPreviewOut>(
      await request.post("/api/health/events/preview", {
        data: { scope: "batch", purchase_batch_id: batchId },
        headers: farmHeaders,
      }),
      200,
    );
    expect(preview).toEqual({
      scope: "batch",
      bucket: null,
      purchase_batch_id: batchId,
      task_id: null,
      target_animal_ids: [purchased.id],
      target_animals: [
        { id: purchased.id, tag_number: purchased.tag_number, name: purchased.name },
      ],
      target_count: 1,
      max_targets: expect.any(Number),
    });
    expect(preview.max_targets).toBeGreaterThanOrEqual(preview.target_count);

    const restrictedAnimal = await createHistoricalAnimal(request, {
      tag_number: uniqueValue("HOLD"),
    });
    const eventPayload = {
      scope: "animal",
      animal_id: restrictedAnimal.id,
      date: today,
      type: "TREATMENT",
      product_name: "Observation only",
      disease_target: "PPR suspicion",
      suspected_scheduled_disease: true,
      authority_notified_at: today,
      isolation_started_at: today,
      notes: "Contract restriction fixture",
    } satisfies HealthEventIn;
    const events = await jsonResponse<HealthEventMutationOut>(
      await request.post("/api/health/events", {
        data: eventPayload,
        headers: farmHeaders,
      }),
      201,
    );
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      animal_id: restrictedAnimal.id,
      animal_tag: restrictedAnimal.tag_number,
      purchase_batch_id: null,
      date: today,
      type: "TREATMENT",
      product_name: eventPayload.product_name,
      disease_target: eventPayload.disease_target,
      suspected_scheduled_disease: true,
      authority_notified_at: today,
      isolation_started_at: today,
    });

    const activeHistory = await jsonResponse<MovementRestrictionHistoryOut>(
      await request.get(`/api/health/restrictions/${restrictedAnimal.id}`, {
        headers: farmHeaders,
      }),
      200,
    );
    expect(activeHistory).toMatchObject({
      animal_id: restrictedAnimal.id,
      restriction_version: 1,
      active: true,
      total: 1,
      limit: 25,
      offset: 0,
    });
    expect(activeHistory.actions).toHaveLength(1);
    expect(activeHistory.actions[0]).toMatchObject({
      restriction_version: 1,
      action: "PLACED",
      disease_target: eventPayload.disease_target,
      health_event_id: events[0].id,
    });

    const clearanceReference = uniqueValue("CLEARANCE");
    await emptyResponse(
      await request.post(`/api/health/restrictions/${restrictedAnimal.id}/clear`, {
        data: {
          clearance_reference: clearanceReference,
          expected_restriction_version: activeHistory.restriction_version,
        },
        headers: farmHeaders,
      }),
      204,
    );

    const clearedHistory = await jsonResponse<MovementRestrictionHistoryOut>(
      await request.get(`/api/health/restrictions/${restrictedAnimal.id}`, {
        headers: farmHeaders,
      }),
      200,
    );
    expect(clearedHistory).toMatchObject({
      animal_id: restrictedAnimal.id,
      restriction_version: 1,
      active: false,
      total: 2,
    });
    expect(clearedHistory.actions.map((action) => action.action)).toEqual([
      "CLEARED",
      "PLACED",
    ]);
    expect(clearedHistory.actions[0]).toMatchObject({
      restriction_version: 1,
      action_reference: clearanceReference,
      disease_target: eventPayload.disease_target,
      health_event_id: null,
    });

    const restrictedProfile = await jsonResponse<AnimalProfileOut>(
      await request.get(`/api/animals/${restrictedAnimal.id}`, { headers: farmHeaders }),
      200,
    );
    expect(restrictedProfile.animal).toMatchObject({
      id: restrictedAnimal.id,
      movement_restricted: false,
      suspected_scheduled_disease: false,
      suspected_disease: null,
      restriction_version: 1,
      restriction_clearance_reference: clearanceReference,
      restriction_cleared_by_id: ownerId,
      restriction_cleared_at: expect.any(String),
    });
    expect(restrictedProfile.health_events.map((event) => event.id)).toContain(events[0].id);
  });

  test("task GET returns the created duty and skip remains visible on readback", async ({
    request,
  }) => {
    const title = uniqueValue("Contract duty");
    const taskPayload = {
      title,
      due_date: farmDate(),
      category: "OTHER",
    } satisfies TaskCreateIn;
    const created = await jsonResponse<TaskOut>(
      await request.post("/api/tasks", { data: taskPayload, headers: farmHeaders }),
      201,
    );
    expect(created).toMatchObject({
      title,
      due_date: taskPayload.due_date,
      category: "OTHER",
      status: "PENDING",
      auto_generated: false,
      assigned_role_id: null,
      assigned_user_id: null,
      skipped_by_id: null,
      skipped_at: null,
      skip_reason: null,
    });

    const directRead = await jsonResponse<TaskOut>(
      await request.get(`/api/tasks/${created.id}`, { headers: farmHeaders }),
      200,
    );
    expect(directRead).toEqual(created);

    const skipPayload = {
      reason: "Superseded by contract verification",
    } satisfies TaskSkipIn;
    const skipped = await jsonResponse<TaskOut>(
      await request.post(`/api/tasks/${created.id}/skip`, {
        data: skipPayload,
        headers: farmHeaders,
      }),
      200,
    );
    expect(skipped).toMatchObject({
      id: created.id,
      title,
      status: "SKIPPED",
      skipped_by_id: ownerId,
      skipped_at: expect.any(String),
      skip_reason: skipPayload.reason,
    });

    const skippedRead = await jsonResponse<TaskOut>(
      await request.get(`/api/tasks/${created.id}`, { headers: farmHeaders }),
      200,
    );
    expect(skippedRead).toEqual(skipped);
  });

  test("feeding setting and recipe mix are reflected by plan, inventory, and finished stock", async ({
    request,
  }) => {
    await createHistoricalAnimal(request, { tag_number: uniqueValue("FEED") });
    const setting = {
      bucket: "FOUNDATION",
      daily_kg_per_head: 1.234,
    } satisfies FeedSettingIn;
    await emptyResponse(
      await request.post("/api/feeding/settings", {
        data: setting,
        headers: farmHeaders,
      }),
      204,
    );
    const plan = await jsonResponse<FeedingPlanOut>(
      await request.get("/api/feeding/plan", { headers: farmHeaders }),
      200,
    );
    const foundationLine = plan.lines.find((line) => line.bucket === setting.bucket);
    expect(foundationLine).toBeDefined();
    expect(foundationLine).toMatchObject({
      bucket: "FOUNDATION",
      kg_per_head: setting.daily_kg_per_head,
      heads: expect.any(Number),
      daily_kg: expect.any(Number),
    });
    expect(foundationLine!.heads).toBeGreaterThan(0);
    expect(foundationLine!.daily_kg).toBeCloseTo(
      foundationLine!.heads * setting.daily_kg_per_head,
      3,
    );

    const recipes = await jsonResponse<RecipeListOut>(
      await request.get("/api/feeding/recipes", { headers: farmHeaders }),
      200,
    );
    const recipe = recipes.recipes.find((item) => item.code === "CREEP");
    expect(recipe).toBeDefined();
    expect(recipe!.lines).toHaveLength(4);

    const initialInventory = await jsonResponse<FeedInventoryOut[]>(
      await request.get("/api/feeding/inventory", { headers: farmHeaders }),
      200,
    );
    const inventoryByIngredient = new Map(
      initialInventory.map((item) => [item.ingredient, item]),
    );
    const restock = { qty_kg: 10 } satisfies StockAddIn;
    for (const line of recipe!.lines ?? []) {
      const item = inventoryByIngredient.get(line.ingredient);
      expect(item, `missing inventory for ${line.ingredient}`).toBeDefined();
      const stocked = await jsonResponse<FeedInventoryOut>(
        await request.post(`/api/feeding/inventory/${item!.id}/add`, {
          data: restock,
          headers: { ...farmHeaders, "Idempotency-Key": crypto.randomUUID() },
        }),
        200,
      );
      expect(stocked).toMatchObject({
        id: item!.id,
        ingredient: line.ingredient,
        qty_on_hand: 10,
        last_purchase_price_per_kg: null,
      });
    }

    const batchKg = 10;
    const mixed = await jsonResponse<FeedRecipeOut>(
      await request.post("/api/feeding/mix", {
        data: { recipe_code: recipe!.code, batch_kg: batchKg },
        headers: { ...farmHeaders, "Idempotency-Key": crypto.randomUUID() },
      }),
      200,
    );
    expect(mixed).toEqual(recipe);

    const finished = await jsonResponse<FinishedFeedStockOut[]>(
      await request.get("/api/feeding/finished-stock", { headers: farmHeaders }),
      200,
    );
    expect(finished).toContainEqual({
      recipe_code: recipe!.code,
      recipe_name: recipe!.name,
      qty_on_hand: batchKg,
    });

    const inventoryAfterMix = await jsonResponse<FeedInventoryOut[]>(
      await request.get("/api/feeding/inventory", { headers: farmHeaders }),
      200,
    );
    for (const line of recipe!.lines ?? []) {
      const item = inventoryAfterMix.find((entry) => entry.ingredient === line.ingredient);
      expect(item).toBeDefined();
      expect(item!.qty_on_hand).toBeCloseTo(
        restock.qty_kg - (batchKg * line.kg_per_100kg) / 100,
        3,
      );
    }
  });

  test("finance correction returns the replacement and preserves the voided source audit", async ({
    request,
  }) => {
    const today = farmDate();
    const originalPayload = {
      date: today,
      type: "EXPENSE",
      category: "EQUIPMENT",
      amount: 125,
      notes: "Contract original",
    } satisfies TransactionIn;
    const original = await jsonResponse<TransactionOut>(
      await request.post("/api/finance/new", {
        data: originalPayload,
        // The mutation requires an idempotency key (retry-safe replay
        // contract); any stable key works for a single submit.
        headers: { ...farmHeaders, "Idempotency-Key": crypto.randomUUID() },
      }),
      201,
    );
    expect(original).toMatchObject({
      date: today,
      type: "EXPENSE",
      category: "EQUIPMENT",
      amount: 125,
      notes: originalPayload.notes,
      correction_of_id: null,
      voided_at: null,
      void_reason: null,
    });

    const correctionPayload = {
      date: today,
      type: "EXPENSE",
      category: "EQUIPMENT",
      amount: 80,
      notes: "Contract corrected",
      reason: "Incorrect supplier invoice amount",
    } satisfies TransactionCorrectionIn;
    const replacement = await jsonResponse<TransactionOut>(
      await request.post(`/api/finance/transactions/${original.id}/correct`, {
        data: correctionPayload,
        headers: farmHeaders,
      }),
      201,
    );
    expect(replacement).toMatchObject({
      date: today,
      type: "EXPENSE",
      category: "EQUIPMENT",
      amount: 80,
      notes: correctionPayload.notes,
      correction_of_id: original.id,
      voided_at: null,
      void_reason: null,
    });

    const ledger = await jsonResponse<FinanceOut>(
      await request.get("/api/finance", {
        headers: farmHeaders,
        params: { category: "EQUIPMENT", limit: 20, offset: 0 },
      }),
      200,
    );
    expect(ledger).toMatchObject({ transactions_total: 2, limit: 20, offset: 0 });
    const persistedOriginal = ledger.transactions.find((item) => item.id === original.id);
    const persistedReplacement = ledger.transactions.find(
      (item) => item.id === replacement.id,
    );
    expect(persistedOriginal).toMatchObject({
      id: original.id,
      amount: 125,
      voided_at: expect.any(String),
      voided_by_id: ownerId,
      void_reason: correctionPayload.reason,
    });
    expect(persistedReplacement).toEqual(replacement);
  });

  test("herd snapshot and scenario GET/PATCH/compare/DELETE stay consistent", async ({
    request,
  }) => {
    test.setTimeout(120_000);
    const before = await jsonResponse<HerdSnapshotOut>(
      await request.get("/api/simulation/herd-snapshot", { headers: farmHeaders }),
      200,
    );
    await createHistoricalAnimal(request, {
      tag_number: uniqueValue("SNAPSHOT"),
      sex: "F",
      date_of_birth: null,
      estimated_dob: null,
    });
    const after = await jsonResponse<HerdSnapshotOut>(
      await request.get("/api/simulation/herd-snapshot", { headers: farmHeaders }),
      200,
    );
    expect(after).toEqual({
      ...before,
      does: before.does + 1,
      total_head: before.total_head + 1,
    });
    expect(after.total_head).toBe(
      after.does +
        after.bucks +
        after.f_kids +
        after.f_weaners +
        after.f_growers +
        after.m_kids +
        after.m_weaners +
        after.m_growers,
    );

    const defaults = await jsonResponse<SimulationAssumptions>(
      await request.get("/api/simulation/defaults", { headers: farmHeaders }),
      200,
    );
    const assumptionsOne: SimulationAssumptions = structuredClone(defaults);
    assumptionsOne.meta = { ...assumptionsOne.meta, horizon_months: 12 };
    assumptionsOne.herd = { ...assumptionsOne.herd, does: 2, bucks: 1 };
    const assumptionsTwo: SimulationAssumptions = structuredClone(assumptionsOne);
    assumptionsTwo.herd = { ...assumptionsTwo.herd, does: 3 };

    const firstName = uniqueValue("Scenario A");
    const secondName = uniqueValue("Scenario B");
    const firstPayload = {
      name: firstName,
      notes: "First proxy scenario",
      assumptions: assumptionsOne,
    } satisfies ScenarioCreateIn;
    const secondPayload = {
      name: secondName,
      notes: "Second proxy scenario",
      assumptions: assumptionsTwo,
    } satisfies ScenarioCreateIn;
    const first = await jsonResponse<ScenarioOut>(
      await request.post("/api/simulation/scenarios", {
        data: firstPayload,
        headers: farmHeaders,
      }),
      201,
    );
    const second = await jsonResponse<ScenarioOut>(
      await request.post("/api/simulation/scenarios", {
        data: secondPayload,
        headers: farmHeaders,
      }),
      201,
    );
    expect(first).toMatchObject({
      farm_id: farmId,
      name: firstName,
      notes: firstPayload.notes,
      assumptions: { meta: { horizon_months: 12 }, herd: { does: 2, bucks: 1 } },
      valid: true,
      validation_error: null,
      revision: 1,
    });
    expect(second).toMatchObject({
      farm_id: farmId,
      name: secondName,
      assumptions: { meta: { horizon_months: 12 }, herd: { does: 3, bucks: 1 } },
      revision: 1,
    });

    const firstRead = await jsonResponse<ScenarioOut>(
      await request.get(`/api/simulation/scenarios/${first.id}`, {
        headers: farmHeaders,
      }),
      200,
    );
    expect(firstRead).toEqual(first);

    const renamed = uniqueValue("Scenario renamed");
    const updatePayload = {
      expected_revision: first.revision,
      name: renamed,
      notes: "Updated through the frontend proxy",
    } satisfies ScenarioUpdateIn;
    const updated = await jsonResponse<ScenarioOut>(
      await request.patch(`/api/simulation/scenarios/${first.id}`, {
        data: updatePayload,
        headers: farmHeaders,
      }),
      200,
    );
    expect(updated).toMatchObject({
      id: first.id,
      farm_id: farmId,
      name: renamed,
      notes: updatePayload.notes,
      assumptions: first.assumptions,
      valid: true,
      validation_error: null,
      revision: 2,
    });
    const updatedRead = await jsonResponse<ScenarioOut>(
      await request.get(`/api/simulation/scenarios/${first.id}`, {
        headers: farmHeaders,
      }),
      200,
    );
    expect(updatedRead).toEqual(updated);

    const compared = await jsonResponse<ScenarioCompareOut>(
      await request.get("/api/simulation/scenarios/compare", {
        headers: farmHeaders,
        params: { ids: `${first.id},${second.id}` },
        timeout: 90_000,
      }),
      200,
    );
    expect(compared.scenarios.map((scenario) => scenario.id)).toEqual([
      first.id,
      second.id,
    ]);
    expect(compared.scenarios.map((scenario) => scenario.name)).toEqual([
      renamed,
      secondName,
    ]);
    expect(compared.results).toHaveLength(2);
    for (const result of compared.results) {
      expect(result.months).toHaveLength(12);
      expect(result.model_version).not.toHaveLength(0);
      expect(result.assumptions_fingerprint).toMatch(/^[a-f0-9]+$/);
      expect(result).toMatchObject({
        metrics: expect.any(Object),
        feed_summary: expect.any(Object),
        project_cost_breakdown: expect.any(Object),
        terminal_value_breakdown: expect.any(Object),
      });
    }

    await emptyResponse(
      await request.delete(`/api/simulation/scenarios/${second.id}`, {
        headers: farmHeaders,
      }),
      204,
    );
    const deletedRead = await request.get(`/api/simulation/scenarios/${second.id}`, {
      headers: farmHeaders,
    });
    expect((await jsonResponse<{ detail: string }>(deletedRead, 404)).detail).toBe(
      "Scenario not found",
    );
    const remaining = await jsonResponse<ScenarioListOut>(
      await request.get("/api/simulation/scenarios", {
        headers: farmHeaders,
        params: { limit: 20, offset: 0 },
      }),
      200,
    );
    expect(remaining).toMatchObject({ total: 1, limit: 20, offset: 0 });
    expect(remaining.items).toHaveLength(1);
    expect(remaining.items[0]).toEqual(updated);
  });
});
