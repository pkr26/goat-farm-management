/**
 * Disease-check walkthrough dialog — lifecycle suite: opening the dialog
 * must not mint a batch (browsing or losing signal leaves no empty
 * ScreeningBatch rows behind); the first upload attempt creates exactly
 * one batch and drives the constrained presigned POST form end to end.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { DiseaseCheckDialog } from "@/components/screening-check-dialog";
import { server } from "@/test/msw-server";
import { createTestQueryClient, renderWithProviders } from "@/test/render";

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

function renderDialog(onOpenChange = vi.fn()) {
  return renderWithProviders(
    <DiseaseCheckDialog open onOpenChange={onOpenChange} onFinished={vi.fn()} />,
    createTestQueryClient(),
  );
}

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
          form: await request.formData(),
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
          new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], "pen.jpg", {
            type: "image/jpeg",
          }),
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
    const posted = s3Post.mock.calls[0]?.[0] as {
      contentType: string | null;
      form: FormData;
    };
    expect(posted.contentType).toMatch(/^multipart\/form-data; boundary=/);
    expect([...posted.form.keys()]).toEqual([...Object.keys(POST_FIELDS), "file"]);
    expect(posted.form.get("key")).toBe(POST_FIELDS.key);
    expect(posted.form.get("Content-Type")).toBe("image/jpeg");
    const uploadedFile = posted.form.get("file");
    // MSW parses multipart with a different File realm, so assert the wire
    // shape instead of relying on jsdom's `instanceof File` identity.
    expect(uploadedFile).not.toBeNull();
    expect(typeof uploadedFile).not.toBe("string");
    expect(uploadedFile).toMatchObject({ type: "image/jpeg" });
    // The footer counter reflects the uploaded photo.
    expect(await screen.findByText(/1/)).toBeInTheDocument();

    // A second photo reuses the same batch — no second POST /batches.
    fireEvent.change(input!, {
      target: {
        files: [
          new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], "pen2.jpg", {
            type: "image/jpeg",
          }),
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
    await new Promise((resolve) => setTimeout(resolve, 0));
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
});
