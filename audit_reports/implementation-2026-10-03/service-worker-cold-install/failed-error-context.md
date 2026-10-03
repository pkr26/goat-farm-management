# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: worker-tablet-journey.spec.ts >> worker tablet >> PIN login, offline completion, sync with attribution
- Location: e2e/worker-tablet-journey.spec.ts:55:3

# Error details

```
Test timeout of 120000ms exceeded.
```

```
Error: page.evaluate: Test timeout of 120000ms exceeded.
```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - generic [ref=e2]:
    - banner [ref=e3]:
      - generic [ref=e4]:
        - generic [ref=e5]:
          - paragraph [ref=e6]: E2E Farm musr3yfemt
          - paragraph [ref=e7]: Tab Worker musr7ach
        - button "షిఫ్ట్ ముగించు" [ref=e9]
    - main [ref=e10]:
      - generic [ref=e11]:
        - generic [ref=e13]:
          - heading "నా విధులు" [level=1] [ref=e14]
          - paragraph [ref=e15]: ఈ రోజు మరియు ఆలస్యమైన విధులు.
        - region [ref=e16]:
          - heading "ఆలస్యం (1)" [level=2] [ref=e17]
          - list [ref=e18]:
            - listitem [ref=e19]:
              - paragraph [ref=e21]: Tablet duty musr7ach
              - paragraph [ref=e24]: 3, అక్టో 2026
              - generic [ref=e25]:
                - button "పూర్తయింది" [ref=e26]
                - button "వదిలేయి" [ref=e27]
  - region "Notifications alt+T"
  - alert [ref=e28]
```

# Test source

```ts
  68  |       data: {
  69  |         name: `Tablet ${suffix}`,
  70  |         permissions: ["dashboard.view", "tasks.view", "tasks.complete"],
  71  |       },
  72  |     });
  73  |     expect(role.status(), await role.text()).toBe(201);
  74  |     const roleId = ((await role.json()) as { id: number }).id;
  75  | 
  76  |     // PIN-only worker: the API accepts exactly one credential per worker
  77  |     // (a password would ride the must-change fence and never reach the
  78  |     // tablet door).
  79  |     const worker = await ownerApi(request, "/api/team/workers", {
  80  |       method: "POST",
  81  |       data: {
  82  |         name: `Tab Worker ${suffix}`,
  83  |         email: `tab-${suffix}@goatfarm.test`,
  84  |         role_id: roleId,
  85  |         pin: PIN,
  86  |       },
  87  |     });
  88  |     expect(worker.status(), await worker.text()).toBe(201);
  89  |     const { id: membershipId } = (await worker.json()) as { id: number };
  90  | 
  91  |     // A SECOND worker's personal duty must stay invisible on this tablet
  92  |     // (own duties only — the negative half of the scoping assertion).
  93  |     const coworker = await ownerApi(request, "/api/team/workers", {
  94  |       method: "POST",
  95  |       data: {
  96  |         name: `Tab Peer ${suffix}`,
  97  |         email: `peer-${suffix}@goatfarm.test`,
  98  |         password: "tablet-pass-1234",
  99  |         role_id: roleId,
  100 |       },
  101 |     });
  102 |     expect(coworker.status(), await coworker.text()).toBe(201);
  103 |     const { id: peerMembershipId } = (await coworker.json()) as { id: number };
  104 | 
  105 |     const roster = await request.get(
  106 |       `http://localhost:8000/api/auth/worker-roster?farm_id=${farmId}`,
  107 |     );
  108 |     const rosterBody = (await roster.json()) as { items: { membership_id: number }[] };
  109 |     expect(rosterBody.items.some((item) => item.membership_id === membershipId)).toBe(true);
  110 | 
  111 |     // The duty: due today, personally assigned (needs the worker's user id).
  112 |     const teamBody = (await (await ownerApi(request, "/api/team")).json()) as {
  113 |       memberships: { id: number; user_id: number }[];
  114 |     };
  115 |     const me = teamBody.memberships.find((row) => row.id === membershipId);
  116 |     if (!me) throw new Error("membership not found");
  117 |     const peer = teamBody.memberships.find((row) => row.id === peerMembershipId);
  118 |     if (!peer) throw new Error("peer membership not found");
  119 |     const today = new Date().toISOString().slice(0, 10);
  120 |     const duty = await ownerApi(request, "/api/tasks", {
  121 |       method: "POST",
  122 |       data: {
  123 |         title: `Tablet duty ${suffix}`,
  124 |         due_date: today,
  125 |         category: "OTHER",
  126 |         assigned_user_id: me.user_id,
  127 |       },
  128 |     });
  129 |     expect(duty.status(), await duty.text()).toBe(201);
  130 |     const dutyId = ((await duty.json()) as { id: number }).id;
  131 | 
  132 |     // A personal duty of the PEER worker that must never render on this
  133 |     // board (the negative half of the own-duties-only assertion).
  134 |     const peerDuty = await ownerApi(request, "/api/tasks", {
  135 |       method: "POST",
  136 |       data: {
  137 |         title: `Peer duty ${suffix}`,
  138 |         due_date: today,
  139 |         category: "OTHER",
  140 |         assigned_user_id: peer.user_id,
  141 |       },
  142 |     });
  143 |     expect(peerDuty.status(), await peerDuty.text()).toBe(201);
  144 | 
  145 |     // --- Pin the tablet and sign in by PIN. -------------------------------
  146 |     await page.addInitScript((farm) => {
  147 |       window.localStorage.setItem("herdly.tabletFarm", String(farm));
  148 |     }, farmId);
  149 |     await page.goto("/worker/login");
  150 |     await page.getByRole("button", { name: new RegExp(`Tab Worker ${suffix}`) }).click();
  151 |     // Language-stable hooks: the surface is Telugu-first, so aria-labels
  152 |     // localize but the testids do not.
  153 |     for (const digit of PIN) {
  154 |       await page.getByTestId(`pin-key-${digit}`).click();
  155 |     }
  156 |     await page.getByTestId("pin-sign-in").click();
  157 |     await expect(page).toHaveURL(/\/worker$/, { timeout: 20_000 });
  158 | 
  159 |     // Own duties only: this worker sees exactly their personal duties —
  160 |     // never the peer's (the board is task_scope'd to the signed-in worker).
  161 |     await expect(page.getByText(`Tablet duty ${suffix}`)).toBeVisible({ timeout: 20_000 });
  162 |     await expect(page.getByText(`Peer duty ${suffix}`)).toHaveCount(0);
  163 |     const dutyCount = await page.getByTestId(/^worker-duty-\d+$/).count();
  164 |     expect(dutyCount).toBeGreaterThanOrEqual(1);
  165 | 
  166 |     // Offline capability is ready only when shell assets and the authorized
  167 |     // current-shift snapshot have committed. The next page was never visited.
> 168 |     await page.evaluate(() => navigator.serviceWorker.ready);
      |                ^ Error: page.evaluate: Test timeout of 120000ms exceeded.
  169 |     await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null)).toBe(true);
  170 |     await expect.poll(() => page.evaluate(() => sessionStorage.getItem("herdly:offline-shift:v1") !== null)).toBe(true);
  171 | 
  172 |     // --- Complete OFFLINE: the write queues with its idempotency key. -----
  173 |     const context = page.context();
  174 |     await context.setOffline(true);
  175 |     await page.goto("/worker/offline");
  176 |     await expect(page.getByTestId(`offline-complete-${dutyId}`)).toBeVisible({ timeout: 15_000 });
  177 |     await page.getByTestId(`offline-complete-${dutyId}`).click();
  178 |     await expect(page.getByTestId(`offline-duty-${dutyId}`).getByRole("status")).toBeVisible();
  179 |     await page.reload();
  180 |     await expect(page.getByTestId(`offline-duty-${dutyId}`).getByRole("status")).toBeVisible({ timeout: 15_000 });
  181 |     await expect(page.getByTestId(`offline-complete-${dutyId}`)).toHaveCount(0);
  182 | 
  183 |     // --- Reconnect: the online event drains the queue. --------------------
  184 |     await context.setOffline(false);
  185 |     await page.goto("/worker");
  186 |     await expect(page.getByTestId("worker-queue-depth")).toHaveCount(0, { timeout: 30_000 });
  187 | 
  188 |     // The duty is DONE and attributed to the worker (exactly once).
  189 |     await expect
  190 |       .poll(
  191 |         async () => {
  192 |           const state = await ownerApi(request, `/api/tasks/${dutyId}`);
  193 |           if (!state.ok()) return null;
  194 |           const body = (await state.json()) as {
  195 |             status: string;
  196 |             completed_by_id: number | null;
  197 |           };
  198 |           return `${body.status}|${body.completed_by_id}`;
  199 |         },
  200 |         { timeout: 30_000 },
  201 |       )
  202 |       .toBe(`DONE|${me.user_id}`);
  203 | 
  204 |     // End shift closes the cached shift and returns to the tablet login.
  205 |     await page.getByTestId("end-shift").click();
  206 |     await expect(page).toHaveURL(/\/worker\/login$/, { timeout: 20_000 });
  207 |     expect(await page.evaluate(() => sessionStorage.getItem("herdly:offline-shift:v1"))).toBeNull();
  208 |   });
  209 | 
  210 |   test("form-linked duty deep-links to its form and carries returnTo=/worker", async ({
  211 |     page,
  212 |     request,
  213 |   }) => {
  214 |     test.setTimeout(180_000);
  215 |     const suffix = `f${Date.now().toString(36)}`;
  216 |     const farmProbe = await ownerApi(request, "/api/auth/farms");
  217 |     const farmId = ((await farmProbe.json()) as unknown as { id: number }[])[0].id;
  218 | 
  219 |     // Ultrasound duties are generated (never manually created) and
  220 |     // auto-assigned to the farm's seeded VET preset role — whose permission
  221 |     // set includes breeding.manage, exactly what permittedTaskActionPath
  222 |     // needs to render the deep link. So: vet worker + backdated breeding.
  223 |     const teamPage = (await (await ownerApi(request, "/api/team")).json()) as {
  224 |       roles: { id: number; code: string | null }[];
  225 |     };
  226 |     const vetRole = teamPage.roles.find((r) => r.code === "VET");
  227 |     if (!vetRole) throw new Error("seeded VET role not found");
  228 | 
  229 |     const worker = await ownerApi(request, "/api/team/workers", {
  230 |       method: "POST",
  231 |       data: {
  232 |         name: `Form Worker ${suffix}`,
  233 |         email: `form-${suffix}@goatfarm.test`,
  234 |         role_id: vetRole.id,
  235 |         pin: PIN,
  236 |       },
  237 |     });
  238 |     expect(worker.status(), await worker.text()).toBe(201);
  239 | 
  240 |     const breedingDate = daysAgo(40);
  241 |     const doeTag = `E2E-WDOE-${suffix}`.toUpperCase();
  242 |     const buckTag = `E2E-WBUCK-${suffix}`.toUpperCase();
  243 |     const doe = await ownerApi(request, "/api/animals", {
  244 |       method: "POST",
  245 |       data: {
  246 |         tag_number: doeTag,
  247 |         sex: "F",
  248 |         source: "BORN",
  249 |         historical_import_reason: "E2E worker tablet doe fixture",
  250 |         current_bucket: "FOUNDATION",
  251 |         date_of_birth: monthsAgo(20),
  252 |         weight_kg: 24,
  253 |         weight_date: breedingDate,
  254 |       },
  255 |     });
  256 |     expect(doe.status(), await doe.text()).toBe(201);
  257 |     const doeId = ((await doe.json()) as { id: number }).id;
  258 | 
  259 |     const buck = await ownerApi(request, "/api/animals", {
  260 |       method: "POST",
  261 |       data: {
  262 |         tag_number: buckTag,
  263 |         sex: "M",
  264 |         source: "BORN",
  265 |         historical_import_reason: "E2E worker tablet buck fixture",
  266 |         current_bucket: "BREEDING",
  267 |         date_of_birth: monthsAgo(20),
  268 |         weight_kg: 30,
```