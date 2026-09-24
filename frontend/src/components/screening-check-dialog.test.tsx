/**
 * Disease-check walkthrough dialog — lifecycle suite: opening the dialog
 * must not mint a batch (browsing or losing signal leaves no empty
 * ScreeningBatch rows behind); the first upload attempt creates exactly
 * one batch and drives the constrained presigned POST form end to end.
 */

import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";

import { DiseaseCheckDialog } from "@/components/screening-check-dialog";
import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";
import { settle } from "@/test/settle";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
const toastMock = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/screening",
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const BOARD_ROW = {
  bucket: "BREEDING",
  name: "Breeding does",
  who: "Open",
  exit_rule: "none",
  daily_kg_per_head: 0,
  animals: [],
  animals_total: 4,
  animals_limit: 12,
  animals_page_path: "/animals?bucket=BREEDING",
};

const POST_FIELDS = {
  key: "raw/1/2026-09-17/BREEDING/9-abc.jpg",
  "Content-Type": "image/jpeg",
  policy: "test-policy",
  "x-amz-algorithm": "AWS4-HMAC-SHA256",
};

/** jsdom's File implements Blob#arrayBuffer but not Blob#stream, so Node's
 * fetch (the wire MSW intercepts) cannot serialize its bytes as a multipart
 * file part — a browser always has stream(). Bridge the realm with a real
 * ReadableStream so the generated part carries the file's name/type like
 * production; the byte payload itself stays a realm artifact we do not
 * assert on. */
function browserGradeFile(bytes: Uint8Array<ArrayBuffer>, name: string, type: string): File {
  const file = new File([bytes], name, { type });
  Object.defineProperty(file, "stream", {
    value: () =>
      new ReadableStream({
        start(controller) {
          controller.enqueue(bytes);
          controller.close();
        },
      }),
  });
  return file;
}

/** Assert the S3 POST's multipart wire shape from the raw body: field ORDER
 * (every signed field must precede `file`), field values, and the multipart
 * content type with a boundary. Undici's request.formData() cannot re-parse
 * a jsdom-originated file part (its parser rejects the cross-realm File),
 * so the assertions ride the pre-parsed wire text instead. */
function expectS3MultipartWire(posted: { contentType: string | null; raw: string }) {
  expect(posted.contentType).toMatch(/^multipart\/form-data; boundary=/);
  const names = [...posted.raw.matchAll(/Content-Disposition: form-data; name="([^"]+)"/g)].map(
    (match) => match[1],
  );
  expect(names).toEqual([...Object.keys(POST_FIELDS), "file"]);
  for (const [name, value] of Object.entries(POST_FIELDS)) {
    const partIndex = posted.raw.indexOf(`name="${name}"`);
    expect(partIndex).toBeGreaterThanOrEqual(0);
    expect(posted.raw.slice(partIndex)).toContain(value);
  }
  // The file part is a real attachment: it carries its own Content-Type
  // header (the component must NOT set a request-level Content-Type — the
  // browser supplies the boundary).
  const fileIndex = posted.raw.indexOf('name="file"');
  expect(fileIndex).toBeGreaterThan(posted.raw.indexOf('name="x-amz-algorithm"'));
  expect(posted.raw.slice(fileIndex)).toContain("Content-Type: image/jpeg");
}

function renderDialog(onOpenChange = vi.fn()) {
  return renderWithProviders(
    <DiseaseCheckDialog open onOpenChange={onOpenChange} onFinished={vi.fn()} />,
    createTestQueryClient(),
  );
}

// Toast calls accumulate across tests in one file (the sonner mock is
// module-level); without clearing, a toast asserted by a LATER test can be
// satisfied by an EARLIER test's call — which masks exactly the silent-path
// mutants the hardening rounds below target.
beforeEach(() => {
  toastMock.success.mockClear();
  toastMock.error.mockClear();
});

describe("DiseaseCheckDialog", () => {
  it("shows the bucket choices without minting a batch on open", async () => {
    const batchPost = vi.fn();
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () => {
        batchPost();
        return HttpResponse.json(
          { id: 7, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        );
      }),
    );

    renderDialog();

    expect(await screen.findByText("Breeding")).toBeInTheDocument();
    await waitFor(() => {
      expect(batchPost).not.toHaveBeenCalled();
    });
  });

  it("creates the batch lazily and POSTs every signed field before the file", async () => {
    const user = userEvent.setup();
    const batchPost = vi.fn();
    const uploadPost = vi.fn();
    const s3Post = vi.fn();
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () => {
        batchPost();
        return HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        );
      }),
      http.post("/api/screening/uploads", async ({ request }) => {
        uploadPost(await request.json());
        return HttpResponse.json(
          {
            image_id: 31,
            s3_key: "raw/1/2026-09-17/BREEDING/9-abc.jpg",
            upload_url: "https://fake-s3.test/raw/1/2026-09-17/BREEDING/9-abc.jpg",
            upload_method: "POST",
            upload_fields: POST_FIELDS,
            max_upload_bytes: 25 * 1024 * 1024,
            expires_in_seconds: 900,
          },
          { status: 201 },
        );
      }),
      http.post("https://fake-s3.test/*", async ({ request }) => {
        s3Post({
          contentType: request.headers.get("content-type"),
          raw: await request.text(),
        });
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderDialog();
    const bucketCard = await screen.findByText("Breeding");
    await user.click(bucketCard);

    const input = document.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, {
      target: {
        files: [
          browserGradeFile(new Uint8Array([0xff, 0xd8, 0xff, 0xe0]), "pen.jpg", "image/jpeg"),
        ],
      },
    });

    const uploadButton = await screen.findByRole("button", { name: /upload photo/i });
    await user.click(uploadButton);

    await waitFor(() => {
      expect(batchPost).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(uploadPost).toHaveBeenCalledTimes(1);
    });
    expect(uploadPost).toHaveBeenCalledWith(
      expect.objectContaining({ batch_id: 9, bucket: "BREEDING", file_size: 4 }),
    );
    await waitFor(() => {
      expect(s3Post).toHaveBeenCalledTimes(1);
    });
    expectS3MultipartWire(
      s3Post.mock.calls[0]?.[0] as { contentType: string | null; raw: string },
    );
    // The footer counter reflects the uploaded photo.
    expect(await screen.findByText(/1/)).toBeInTheDocument();

    // A second photo reuses the same batch — no second POST /batches.
    fireEvent.change(input!, {
      target: {
        files: [
          browserGradeFile(new Uint8Array([0xff, 0xd8, 0xff, 0xe0]), "pen2.jpg", "image/jpeg"),
        ],
      },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => {
      expect(uploadPost).toHaveBeenCalledTimes(2);
    });
    expect(batchPost).toHaveBeenCalledTimes(1);
  });

  it("rejects an empty photo before it can create a batch or pending upload", async () => {
    const user = userEvent.setup();
    const batchPost = vi.fn();
    const uploadPost = vi.fn();
    const s3Post = vi.fn();
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () => {
        batchPost();
        return HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        );
      }),
      http.post("/api/screening/uploads", () => {
        uploadPost();
        return new HttpResponse(null, { status: 500 });
      }),
      http.post("https://fake-s3.test/*", () => {
        s3Post();
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderDialog();
    await user.click(await screen.findByText("Breeding"));
    const input = document.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, {
      target: {
        files: [new File([], "empty.jpg", { type: "image/jpeg" })],
      },
    });

    await waitFor(() => {
      expect(batchPost).not.toHaveBeenCalled();
      expect(uploadPost).not.toHaveBeenCalled();
      expect(s3Post).not.toHaveBeenCalled();
    });
  });

  it("isolates a reopened walkthrough from a delayed prior batch request", async () => {
    const user = userEvent.setup();
    const batchPost = vi.fn();
    const uploadBodies: Array<{ batch_id: number }> = [];
    const s3Post = vi.fn();
    const firstBatchReturned = vi.fn();
    let releaseFirstBatch = () => {};
    const firstBatchGate = new Promise<void>((resolve) => {
      releaseFirstBatch = resolve;
    });

    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", async () => {
        batchPost();
        if (batchPost.mock.calls.length === 1) {
          await firstBatchGate;
          firstBatchReturned();
          return HttpResponse.json(
            { id: 101, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
            { status: 201 },
          );
        }
        return HttpResponse.json(
          { id: 202, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        );
      }),
      http.post("/api/screening/uploads", async ({ request }) => {
        uploadBodies.push((await request.json()) as { batch_id: number });
        return HttpResponse.json(
          {
            image_id: 31,
            s3_key: POST_FIELDS.key,
            upload_url: "https://fake-s3.test/raw/1/2026-09-17/BREEDING/9-abc.jpg",
            upload_method: "POST",
            upload_fields: POST_FIELDS,
            max_upload_bytes: 25 * 1024 * 1024,
            expires_in_seconds: 900,
          },
          { status: 201 },
        );
      }),
      http.post("https://fake-s3.test/*", () => {
        s3Post();
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const onOpenChange = vi.fn();
    const view = renderWithProviders(
      <DiseaseCheckDialog open onOpenChange={onOpenChange} onFinished={vi.fn()} />,
      createTestQueryClient(),
    );
    await user.click(await screen.findByText("Breeding"));
    let input = document.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, {
      target: { files: [new File(["first"], "first.jpg", { type: "image/jpeg" })] },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(batchPost).toHaveBeenCalledTimes(1));

    // The old create stays on the wire while this dialog is closed and
    // reopened. The new walkthrough must be able to start immediately.
    view.rerender(<DiseaseCheckDialog open={false} onOpenChange={onOpenChange} onFinished={vi.fn()} />);
    view.rerender(<DiseaseCheckDialog open onOpenChange={onOpenChange} onFinished={vi.fn()} />);
    await user.click(await screen.findByText("Breeding"));
    input = document.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, {
      target: { files: [new File(["second"], "second.jpg", { type: "image/jpeg" })] },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));

    await waitFor(() => expect(batchPost).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(uploadBodies).toEqual([expect.objectContaining({ batch_id: 202 })]));
    await waitFor(() => expect(s3Post).toHaveBeenCalledTimes(1));

    // Resolving the old create cannot write batch 101 into the reopened
    // session or trigger its stale upload.
    releaseFirstBatch();
    await waitFor(() => expect(firstBatchReturned).toHaveBeenCalledTimes(1));
    await settle(0);
    expect(uploadBodies).toHaveLength(1);

    fireEvent.change(input!, {
      target: { files: [new File(["third"], "third.jpg", { type: "image/jpeg" })] },
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /upload photo/i })).not.toBeDisabled();
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(uploadBodies).toHaveLength(2));
    expect(uploadBodies[1]).toMatchObject({ batch_id: 202 });
  });

  it("surfaces a quota-exceeded upload as the failed-upload toast, with no S3 POST", async () => {
    const user = userEvent.setup();
    const batchPost = vi.fn();
    const s3Post = vi.fn();
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () => {
        batchPost();
        return HttpResponse.json(
          { id: 11, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        );
      }),
      // Per-farm in-flight ceiling reached: the API refuses to presign.
      http.post("/api/screening/uploads", () =>
        HttpResponse.json({ detail: "Too many in-flight images for this farm" }, { status: 429 }),
      ),
      http.post("https://fake-s3.test/*", () => {
        s3Post();
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderDialog();
    await user.click(await screen.findByText("Breeding"));
    const input = document.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, {
      target: { files: [new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], "pen.jpg", { type: "image/jpeg" })] },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));

    // The generic failure toast (the quota reason rides the server detail;
    // the operator remedy is the same: retry later), and nothing was sent
    // to object storage.
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        "Upload failed — check your connection and retry.",
      ),
    );
    await waitFor(() => {
      expect(batchPost).toHaveBeenCalledTimes(1);
    });
    expect(s3Post).not.toHaveBeenCalled();
  });

  it("keeps the walkthrough usable after a provider-side presign failure (5xx)", async () => {
    const user = userEvent.setup();
    let presignAttempts = 0;
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 12, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", () => {
        presignAttempts += 1;
        if (presignAttempts === 1) {
          return new HttpResponse(null, { status: 503 });
        }
        return HttpResponse.json(
          {
            image_id: 41,
            s3_key: "raw/1/2026-09-17/BREEDING/12-abc.jpg",
            upload_url: "https://fake-s3.test/raw/1/2026-09-17/BREEDING/12-abc.jpg",
            upload_method: "POST",
            upload_fields: POST_FIELDS,
            max_upload_bytes: 25 * 1024 * 1024,
            expires_in_seconds: 900,
          },
          { status: 201 },
        );
      }),
      http.post("https://fake-s3.test/*", () => new HttpResponse(null, { status: 204 })),
    );

    renderDialog();
    await user.click(await screen.findByText("Breeding"));
    const input = document.querySelector('input[type="file"]');
    expect(input).not.toBeNull();
    const pick = () =>
      fireEvent.change(input!, {
        target: { files: [new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], "pen.jpg", { type: "image/jpeg" })] },
      });

    pick();
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        "Upload failed — check your connection and retry.",
      ),
    );

    // The failed attempt leaves the walkthrough alive: re-picking the same
    // photo and retrying presigns successfully on the second attempt.
    toastMock.error.mockClear();
    pick();
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => {
      expect(presignAttempts).toBe(2);
    });
  });
});

/** Mutation-hardening round (2026-09-23 deep-mutation campaign): the upload
 *  pipeline's numeric contracts were unpinned — the exact 25 MiB ceiling,
 *  the 1-byte floor, per-bucket and total photo counts ("N uploaded"),
 *  the .png/.jpg extension rule, the default POST upload method, the 60 s
 *  abort budget, the 409-vs-other error split, stale-session fences on the
 *  request-upload leg, and the finish flow's status/epoch/label wiring. */
describe("DiseaseCheckDialog upload contracts", () => {
  const MAX = 25 * 1024 * 1024;

  function stubHappyPath(capture: { uploads: Array<Record<string, unknown>>; s3: number }) {
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", async ({ request }) => {
        capture.uploads.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json(
          {
            image_id: 31,
            s3_key: POST_FIELDS.key,
            upload_url: "https://fake-s3.test/raw/1/2026-09-17/BREEDING/9-abc.jpg",
            upload_method: "POST",
            upload_fields: POST_FIELDS,
            max_upload_bytes: MAX,
            expires_in_seconds: 900,
          },
          { status: 201 },
        );
      }),
      http.post("https://fake-s3.test/*", () => {
        capture.s3 += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
  }

  async function pickBucketAndChoose(
    user: ReturnType<typeof userEvent.setup>,
    file: File,
  ) {
    await user.click(await screen.findByText("Breeding"));
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, { target: { files: [file] } });
  }

  it("accepts exactly 25 MiB and one byte, rejects 25 MiB + 1 and zero bytes", async () => {
    const capture = { uploads: [] as Array<Record<string, unknown>>, s3: 0 };
    stubHappyPath(capture);
    const user = userEvent.setup();
    renderDialog();

    await pickBucketAndChoose(
      user,
      browserGradeFile(new Uint8Array(MAX), "max.jpg", "image/jpeg"),
    );
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(capture.s3).toBe(1));
    expect(capture.uploads[0]).toMatchObject({ file_size: MAX });
    toastMock.error.mockClear();

    await pickBucketAndChoose(
      user,
      browserGradeFile(new Uint8Array(MAX + 1), "over.jpg", "image/jpeg"),
    );
    await settle();
    expect(toastMock.error).toHaveBeenCalledWith("Upload failed — check your connection and retry.");
    expect(capture.uploads).toHaveLength(1);
    toastMock.error.mockClear();

    await pickBucketAndChoose(user, new File([new Uint8Array([1])], "one.jpg", { type: "image/jpeg" }));
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(capture.s3).toBe(2));
    expect(toastMock.error).not.toHaveBeenCalled();
    toastMock.error.mockClear();

    await pickBucketAndChoose(user, new File([], "zero.jpg", { type: "image/jpeg" }));
    await settle();
    expect(toastMock.error).toHaveBeenCalled();
    expect(capture.uploads).toHaveLength(2);
  });

  it("counts each bucket's photos and the total, exactly", async () => {
    const capture = { uploads: [] as Array<Record<string, unknown>>, s3: 0 };
    stubHappyPath(capture);
    const user = userEvent.setup();
    renderDialog();

    expect(screen.getByText("0 uploaded")).toBeInTheDocument();
    await pickBucketAndChoose(
      user,
      browserGradeFile(new Uint8Array([1]), "a.jpg", "image/jpeg"),
    );
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(capture.s3).toBe(1));
    expect(await screen.findByText("1 uploaded")).toBeInTheDocument();

    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, {
      target: { files: [browserGradeFile(new Uint8Array([1]), "b.jpg", "image/jpeg")] },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(capture.s3).toBe(2));
    expect(await screen.findByText("2 uploaded")).toBeInTheDocument();

    // The finish button is enabled exactly when at least one photo exists.
    expect(screen.getByRole("button", { name: /finish & process/i })).toBeEnabled();
  });

  it("refuses to finish with zero photos and never calls submit then", async () => {
    const capture = { uploads: [] as Array<Record<string, unknown>>, s3: 0 };
    stubHappyPath(capture);
    const submitPost = vi.fn();
    server.use(
      http.post("/api/screening/batches/9/submit", () => {
        submitPost();
        return HttpResponse.json({}, { status: 200 });
      }),
    );
    renderDialog();

    const finish = await screen.findByRole("button", { name: /finish & process/i });
    expect(finish).toBeDisabled();
    await settle();
    expect(submitPost).not.toHaveBeenCalled();
    expect(toastMock.error).not.toHaveBeenCalledWith(
      "Upload at least one photo before finishing.",
    );
  });

  it("names the pending upload by type: png gets .png, jpeg gets .jpg", async () => {
    const capture = { uploads: [] as Array<Record<string, unknown>>, s3: 0 };
    stubHappyPath(capture);
    const user = userEvent.setup();
    renderDialog();

    await pickBucketAndChoose(user, new File([new Uint8Array([1])], "x.png", { type: "image/png" }));
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(capture.uploads).toHaveLength(1));
    expect(capture.uploads[0]).toMatchObject({ file_name: "photo.png", content_type: "image/png" });
  });

  it("defaults the S3 method to POST and budgets exactly 60 s", async () => {
    const capture = { uploads: [] as Array<Record<string, unknown>>, s3: 0 };
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", async () => {
        capture.uploads.push({});
        // upload_method omitted: the component must default the S3 call to POST.
        return HttpResponse.json(
          {
            image_id: 31,
            s3_key: POST_FIELDS.key,
            upload_url: "https://fake-s3.test/raw/1/x.jpg",
            upload_fields: POST_FIELDS,
            max_upload_bytes: MAX,
            expires_in_seconds: 900,
          },
          { status: 201 },
        );
      }),
      http.post("https://fake-s3.test/*", () => {
        capture.s3 += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const realTimeout = AbortSignal.timeout.bind(AbortSignal);
    const timeoutSpy = vi
      .spyOn(AbortSignal, "timeout")
      .mockImplementation((ms) => realTimeout(ms) as AbortSignal);
    const user = userEvent.setup();
    renderDialog();

    await pickBucketAndChoose(user, new File([new Uint8Array([1])], "y.jpg", { type: "image/jpeg" }));
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(capture.s3).toBe(1));
    // msw/undici also schedule their own 60 s timeouts — every recorded
    // call must still be exactly the component's budget.
    expect(timeoutSpy.mock.calls.length).toBeGreaterThan(0);
    expect(timeoutSpy.mock.calls.every((c) => c[0] === 60_000)).toBe(true);
    timeoutSpy.mockRestore();
  });

  it("a 409 from the pipeline says no-batch; other failures say upload-failed", async () => {
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () => HttpResponse.json({}, { status: 409 })),
    );
    const user = userEvent.setup();
    renderDialog();

    await pickBucketAndChoose(user, new File([new Uint8Array([1])], "z.jpg", { type: "image/jpeg" }));
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith("Could not start the walkthrough — try again."),
    );

    // Non-409 failures get the other message: the create succeeds but the
    // pending-upload leg fails with 500.
    toastMock.error.mockClear();
    cleanup();
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 10, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", () => HttpResponse.json({}, { status: 500 })),
    );
    const user2 = userEvent.setup();
    renderDialog();
    await pickBucketAndChoose(user2, new File([new Uint8Array([1])], "z.jpg", { type: "image/jpeg" }));
    await user2.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        "Upload failed — check your connection and retry.",
      ),
    );
  });

  it("a stale request-upload response after closing must not reach S3", async () => {
    const s3Post = vi.fn();
    let releaseUploads = () => {};
    const uploadsGate = new Promise<void>((resolve) => {
      releaseUploads = resolve;
    });
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", async () => {
        await uploadsGate;
        return HttpResponse.json(
          {
            image_id: 31,
            s3_key: POST_FIELDS.key,
            upload_url: "https://fake-s3.test/raw/1/x.jpg",
            upload_method: "POST",
            upload_fields: POST_FIELDS,
            max_upload_bytes: MAX,
            expires_in_seconds: 900,
          },
          { status: 201 },
        );
      }),
      http.post("https://fake-s3.test/*", () => {
        s3Post();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    const view = renderWithProviders(
      <DiseaseCheckDialog open onOpenChange={onOpenChange} onFinished={vi.fn()} />,
      createTestQueryClient(),
    );

    await pickBucketAndChoose(user, new File([new Uint8Array([1])], "q.jpg", { type: "image/jpeg" }));
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    view.rerender(
      <DiseaseCheckDialog open={false} onOpenChange={onOpenChange} onFinished={vi.fn()} />,
    );
    releaseUploads();
    await settle(150);
    // The S3 leg never fires for the dead session.
    expect(s3Post).not.toHaveBeenCalled();
  });

  it("a failed request-upload never reaches S3, fresh or stale", async () => {
    const s3Post = vi.fn();
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", () => HttpResponse.json({}, { status: 500 })),
      http.post("https://fake-s3.test/*", () => {
        s3Post();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderDialog();

    await pickBucketAndChoose(user, new File([new Uint8Array([1])], "w.jpg", { type: "image/jpeg" }));
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith(
        "Upload failed — check your connection and retry.",
      ),
    );
    await settle();
    expect(s3Post).not.toHaveBeenCalled();
    expect(screen.getByText("0 uploaded")).toBeInTheDocument();
  });

  it("finishing: pending label, 200-only success, dialog closed, onFinished fired", async () => {
    const capture = { uploads: [] as Array<Record<string, unknown>>, s3: 0 };
    stubHappyPath(capture);
    let releaseSubmit = () => {};
    const submitGate = new Promise<void>((resolve) => {
      releaseSubmit = resolve;
    });
    server.use(
      http.post("/api/screening/batches/9/submit", async () => {
        await submitGate;
        return HttpResponse.json({}, { status: 200 });
      }),
    );
    const onOpenChange = vi.fn();
    const onFinished = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <DiseaseCheckDialog open onOpenChange={onOpenChange} onFinished={onFinished} />,
      createTestQueryClient(),
    );

    await pickBucketAndChoose(
      user,
      browserGradeFile(new Uint8Array([1]), "f.jpg", "image/jpeg"),
    );
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(capture.s3).toBe(1));
    await user.click(screen.getByRole("button", { name: /finish & process/i }));
    expect(await screen.findByText("Finishing…")).toBeInTheDocument();
    expect(onFinished).not.toHaveBeenCalled();

    releaseSubmit();
    await waitFor(() => expect(onFinished).toHaveBeenCalled());
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(toastMock.success).toHaveBeenCalled();
  });
});

/** Round 2: reopen-resets state, in-flight busy wiring, the uploads-leg 409,
 *  finish-submit failure toast, and the grid cards' per-bucket zero counts. */
describe("DiseaseCheckDialog state, busy and error wiring", () => {
  const MAX = 25 * 1024 * 1024;

  function happyPath(s3: { n: number }) {
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", () =>
        HttpResponse.json(
          {
            image_id: 31,
            s3_key: POST_FIELDS.key,
            upload_url: "https://fake-s3.test/raw/1/x.jpg",
            upload_method: "POST",
            upload_fields: POST_FIELDS,
            max_upload_bytes: MAX,
            expires_in_seconds: 900,
          },
          { status: 201 },
        ),
      ),
      http.post("https://fake-s3.test/*", () => {
        s3.n += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
  }

  it("a reopened walkthrough starts clean: no preview, no pending file", async () => {
    const s3 = { n: 0 };
    happyPath(s3);
    const user = userEvent.setup();
    const view = renderWithProviders(
      <DiseaseCheckDialog open onOpenChange={vi.fn()} onFinished={vi.fn()} />,
      createTestQueryClient(),
    );

    await user.click(await screen.findByText("Breeding"));
    // Placeholder (no preview yet): the img with alt "Take photo" is absent.
    expect(screen.queryByRole("img", { name: /take photo/i })).toBeNull();
    expect(screen.getByRole("button", { name: /take photo/i })).toBeInTheDocument();

    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([1])], "p.jpg", { type: "image/jpeg" })] },
    });
    // Pending file flips the labels: preview img appears, button says Retake.
    expect(await screen.findByRole("img", { name: /take photo/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retake/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^take photo$/i })).toBeNull();

    view.rerender(
      <DiseaseCheckDialog open={false} onOpenChange={vi.fn()} onFinished={vi.fn()} />,
    );
    view.rerender(
      <DiseaseCheckDialog open onOpenChange={vi.fn()} onFinished={vi.fn()} />,
    );
    // The reopened walkthrough is back on the bucket grid with every piece
    // of session state cleared — no stale preview or pending photo.
    expect(await screen.findByText("Breeding")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /retake/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /take photo/i })).toBeNull();
    expect(screen.getByRole("button", { name: /finish & process/i })).toBeDisabled();
  });

  it("shows the busy state and locks both buttons while an upload is in flight", async () => {
    let releaseUploads = () => {};
    const gate = new Promise<void>((resolve) => {
      releaseUploads = resolve;
    });
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", async () => {
        await gate;
        return HttpResponse.json(
          {
            image_id: 31,
            s3_key: POST_FIELDS.key,
            upload_url: "https://fake-s3.test/raw/1/x.jpg",
            upload_method: "POST",
            upload_fields: POST_FIELDS,
            max_upload_bytes: MAX,
            expires_in_seconds: 900,
          },
          { status: 201 },
        );
      }),
    );
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByText("Breeding"));
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([1])], "p.jpg", { type: "image/jpeg" })] },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));

    expect(screen.getByRole("button", { name: /uploading…/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /retake/i })).toBeDisabled();
    releaseUploads();
    http.post("https://fake-s3.test/*", () => new HttpResponse(null, { status: 204 }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /upload photo/i })).toBeEnabled(),
    );
  });

  it("grid cards start at zero uploaded and a 409 from the uploads leg says no-batch", async () => {
    const uploadsPost = vi.fn();
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () =>
        HttpResponse.json(
          { id: 9, created_at: "2026-09-17T00:00:00Z", submitted_at: null },
          { status: 201 },
        ),
      ),
      http.post("/api/screening/uploads", () => {
        uploadsPost();
        return HttpResponse.json({}, { status: 409 });
      }),
    );
    const user = userEvent.setup();
    renderDialog();

    // Both the bucket card and the footer show an exact zero.
    await waitFor(() => expect(screen.getAllByText("0 uploaded").length).toBe(2));

    await user.click(screen.getByText("Breeding"));
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([1])], "p.jpg", { type: "image/jpeg" })] },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    expect(await waitFor(() => expect(uploadsPost).toHaveBeenCalledTimes(1)));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith("Could not start the walkthrough — try again."),
    );
    expect(screen.getByText("0 uploaded")).toBeInTheDocument();
  });

  it("a failed batch create never reaches the uploads leg", async () => {
    const uploadsPost = vi.fn();
    server.use(
      http.get("/api/buckets", () => HttpResponse.json([BOARD_ROW])),
      http.post("/api/screening/batches", () => HttpResponse.json({}, { status: 409 })),
      http.post("/api/screening/uploads", () => {
        uploadsPost();
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderDialog();
    await user.click(await screen.findByText("Breeding"));
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([1])], "p.jpg", { type: "image/jpeg" })] },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith("Could not start the walkthrough — try again."),
    );
    await settle();
    expect(uploadsPost).not.toHaveBeenCalled();
  });

  it("a failed finish surfaces the retry toast instead of closing silently", async () => {
    const s3 = { n: 0 };
    happyPath(s3);
    server.use(
      http.post("/api/screening/batches/9/submit", () => HttpResponse.json({}, { status: 500 })),
    );
    const onOpenChange = vi.fn();
    const onFinished = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <DiseaseCheckDialog open onOpenChange={onOpenChange} onFinished={onFinished} />,
      createTestQueryClient(),
    );

    await user.click(await screen.findByText("Breeding"));
    const input = document.querySelector('input[type="file"]')!;
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([1])], "p.jpg", { type: "image/jpeg" })] },
    });
    await user.click(await screen.findByRole("button", { name: /upload photo/i }));
    await waitFor(() => expect(s3.n).toBe(1));
    await user.click(screen.getByRole("button", { name: /finish & process/i }));
    await waitFor(() =>
      expect(toastMock.error).toHaveBeenCalledWith("Could not start the walkthrough — try again."),
    );
    expect(onFinished).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });
});
