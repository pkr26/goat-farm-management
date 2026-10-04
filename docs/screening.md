# Photo screening

[Documentation index](README.md) · [Project overview](../README.md)

## Registration and processing

Photos registered through the upload flow are screened by a vision model for visible
disease signs. A healthy gate verdict ends that crop's cascade; flagged photos continue
to specialist and cross-check calls. Results are review flags for the veterinary team.

- **Upload registration**: the API chooses each `raw/<farm>/<date>/<bucket>/…` key and
  persists a PENDING row before it returns a presigned **POST** form. The S3 policy
  binds that exact key, a per-row token, the declared JPEG/PNG type, and a 1-byte–25-MiB
  size range. The worker processes registered rows only; direct raw-prefix writes are
  deliberately ignored.
- **Worker**: `screening-worker` compose service (same image as the API, `python -m
  app.worker`). Polls every `GOATFARM_SCREENING_POLL_INTERVAL_SECONDS`, claims
  registered rows fairly across farms (plus retry-eligible PROCESSING/ERROR rows),
  normalizes each photo to a bounded derivative (EXIF stripped, longest edge
  `GOATFARM_SCREENING_IMAGE_MAX_EDGE_PX`). Before any provider call, it durably stores
  that derivative and permanently deletes every version/delete marker of the raw upload.
  A failed raw deletion stops provider disclosure and retries from the sanitized
  derivative. That eager delete does not mark the raw key clean: the original presigned
  form can recreate it until expiry. Every upload therefore retains an indexed cleanup
  obligation due at the exact form expiry plus one hour of clock/final-write slack. The
  API's minute-scale maintenance saga claims finite `FOR UPDATE SKIP LOCKED` pages,
  permanently re-purges and verifies the key, then acknowledges it in a separate
  transaction. A crash after object success safely repeats the idempotent purge; capped
  backoff continues independently of the model retry ceiling. This also covers uploads
  that stop as abandoned, invalid, oversized, duplicate, or attempt-exhausted. Provider
  APIs receive only the sanitized derivative. All retained derivatives/crops are covered
  by the [runtime retention sweep](data-retention.md). The worker skips byte-identical
  duplicates and runs the gate model. Everything is farm-scoped and per-image committed,
  so one bad photo never blocks the batch. Claims commit durably under `FOR UPDATE SKIP
  LOCKED` (two workers can never double-screen a photo), and PENDING rows whose forms
  expire are swept in bounded batches so an abandoned walkthrough cannot occupy the
  queue forever. Its heartbeat health check turns a live-but-failing worker unhealthy
  after `GOATFARM_SCREENING_WORKER_HEALTH_MAX_AGE_SECONDS`.
- **Providers**: `GOATFARM_SCREENING_PROVIDER=anthropic` (Messages API) or
  `openai_compatible` (GLM / GPT / any OpenAI-shaped endpoint). Both answer the
  identical prompt + JSON contract (`app/services/screening/gate.py`).
- **Review**: `GET /api/screening/images` (list, latest verdict, pending finding count)
  and `GET /api/screening/images/{id}` (runs, findings, short-lived presigned photo URL)
  back the frontend **Photo screening** page (health.view permission). Screenings are
  flags for a vet check, not diagnoses.
- **Off by default** (`GOATFARM_SCREENING_ENABLED=false` boots everything as
  configured); enabling with incomplete S3/provider credentials fails startup. All
  settings: [backend/.env.example](../backend/.env.example).

The three audit tables (`screening_images`, `screening_runs`, `screening_findings`)
record provider, model, prompt version and confidence per call — the corpus later
fine-tuning builds on.

**Bucket CORS is a separate deployment requirement.** CSP permits the browser to contact
the configured object-store origin; it does not make S3 accept a cross-origin presigned
POST. Configure the bucket (or equivalent MinIO CORS policy) to answer preflights for
the exact public app origin; the `AllowedOrigins` value must never be `*`. For example,
an AWS S3 bucket used by `https://app.example.com` can use:

```json
[
  {
    "AllowedOrigins": ["https://app.example.com"],
    "AllowedMethods": ["POST", "GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 300
  }
]
```

`AllowedHeaders: ["*"]` above is intentional: the S3 form uses provider signed fields
whose names can change. It does not widen which websites may make the request—the exact
`AllowedOrigins` list enforces that boundary.

For AWS S3 with `GOATFARM_S3_ENDPOINT_URL` unset, presigned URLs use the regional
virtual-host origin `https://<bucket>.s3.<region>.amazonaws.com`; set both runtime CSP
origin lists to that same origin. Keep the CORS allowlist to the public SPA origin, even
when the CSP list names the bucket origin.

## Provider cascade and veterinary review

The cascade runs: **gate → (if flagged) specialists + cross-check**.

- **Round-robin**: `GOATFARM_SCREENING_PROVIDER_ROTATION` is a JSON array of named
  providers (e.g. `claude`, `glm`). The day's ordinal picks the gate primary — Monday
  Claude, Tuesday GLM — with zero stored state; a failed primary falls through to the
  next provider, and the run row records who actually served (`detail.served_by`,
  `detail.fallbacks_failed`).
- **Specialists**: gate observations map to body-region specialists
  (skin/eye/hoof/udder/general) with bounded disease vocabularies (ORF, goat pox,
  ringworm, mange, CL, pinkeye, FAMACHA anemia, foot rot, FMD-suspect, mastitis, …).
  Specialist conditions become the findings; unknown model guesses are coerced to
  `OTHER` with the original kept in the note. If every specialist call fails, the gate's
  own observations remain the findings — the queue is never silently empty.
- **Cross-check**: flagged photos get one second-opinion gate call from the *next*
  provider in the rotation. Disagreement preserves the finding for human review; the page shows
  "models disagree" alongside the provider results.
- **Vet review**: `POST /api/screening/findings/{id}/review` (`health.manage`)
  confirms/rejects a finding with optimistic concurrency (`expected_status`; races get
  409). Confirmed/rejected rows accumulate as the training corpus and carry reviewer +
  timestamp + note.
- The whole-photo cascade uses one gate call when the result is healthy. Enabled crop
  detection adds its own call; each detected crop then has its own gate and any required
  specialist or cross-check calls.

## Per-goat crops, statistics and dataset export

The complete pipeline: **detect → per-goat cascade → vet review → training
corpus**.

- **Multi-goat detection**: one VLM call per photo returns a bounding box per goat
  (0-1000 normalized); each crop runs the full cascade independently, so a healthy goat
  costs one gate call even in a photo where its pen-mate is flagged. Zero detected goats
  — or a failed detection call — falls back to screening the whole photo: a detection
  miss can never leave a herd unscreened. Retries reuse the original boxes and
  re-screen only errored crops, so already-flagged goats never produce duplicate
  findings. Boxes and crop derivatives
  (`screening/<farm>/<date>/v2/images/<image_id>/crops/<crop_id>/<sha>-c<N>.jpg`) are
  persisted. `GOATFARM_SCREENING_CROP_DETECTION_ENABLED=false` restores whole-photo
  behavior; `GOATFARM_SCREENING_MAX_CROPS_PER_IMAGE` caps goats per photo.
- **Provider scoreboard** (`GET /api/screening/stats?days=30`, health.view): gate
  volume, flag rate, error rate, average latency, cross-check agreement, and
  vet-confirmed/rejected/pending finding counts per provider — the measured comparison
  that makes the round-robin a quality tool, not just vendor insurance. Surfaced as the
  "Provider scoreboard" card on the Photo screening page.
- **Dataset export** (`GET /api/screening/export?vet_status=…`, health.manage): every
  reviewed finding with its image/crop S3 keys, detection box, label, severity and vet
  verdict — the fine-tuning corpus. The page's "Export dataset" button downloads it as
  JSON. When the labeled set grows large enough, a fine-tuned classifier can slot in
  behind the same `VisionProvider` seam and the rotation adapts.

## Capture policy and call budget

Choose the capture schedule with the veterinary team, accounting for quarantine,
pregnancy and delivery pens as well as periodic broader reviews. Use the provider
scoreboard to compare flag rate, cross-check agreement and veterinary outcomes on the
farm's own data before changing the provider rotation.

`GOATFARM_SCREENING_DAILY_CALL_BUDGET_PER_FARM` caps the day's provider calls. When a
farm reaches that budget, remaining PENDING photos wait until the next day. Crop
detection, gate, specialist and cross-check calls all contribute to that workload.

## Photo upload walkthrough

The "Disease check" button on the Photo screening page runs the whole on-farm capture
loop with **no AWS credentials on any device**:

1. **Start**: the dialog lets the worker choose a herd bucket. It creates a batch lazily
   on the first upload, so opening and abandoning a walkthrough leaves no empty batch
   behind.
2. **Per pen**: the worker picks a herd bucket (the same pens as the _buckets_ module),
   takes a photo (`capture="environment"` opens the camera), and taps upload per photo.
   The app creates the batch and requests a constrained presigned POST form (`POST
   /api/screening/uploads`) — the server builds the key
   `raw/<farm>/<date>/<bucket>/<batch>-<id>.jpg` and pre-creates the PENDING image row.
   The browser appends every returned `upload_fields` entry and then the `file` as
   multipart form data sent **straight to S3**. The API never proxies photo bytes; S3
   enforces the declared JPEG/PNG type, row token, and 25-MiB maximum.
3. **Finish & process**: `POST /api/screening/batches/{id}/submit` locks the batch; the
   worker's next cycle claims the PENDING rows (an object that has not landed yet is
   quietly re-checked next cycle, never an error) and every photo runs the full cascade
   with its bucket recorded.
4. **Review**: results appear in the review list (filterable by bucket), per-goat crops
   included.

Operational guidance (≥10 clear photos per bucket) is surfaced in the UI rather than
hard-enforced — small pens legitimately have fewer goats. The API rejects queue
amplification above 5 open batches, 100 photos in one batch, or 250 in-flight
PENDING/PROCESSING/ERROR images for a farm. Raw S3 writes that did not originate from
this registration flow remain stored but are never scheduled for screening.

The [Live sandbox contract](../ops/SCREENING_LIVE_CONTRACT.md) checks external
object-store and vision-provider behavior with sandbox credentials.
