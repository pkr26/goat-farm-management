/**
 * Disease-check walkthrough dialog — lifecycle suite: opening the dialog
 * must not mint a batch (browsing or losing signal leaves no empty
 * ScreeningBatch rows behind); the first upload attempt creates exactly
 * one batch and drives the presigned PUT end to end.
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

  it("creates the batch lazily on the first upload and PUTs straight to S3", async () => {
    const user = userEvent.setup();
    const batchPost = vi.fn();
    const uploadPost = vi.fn();
    const s3Put = vi.fn();
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
            expires_in_seconds: 900,
          },
          { status: 201 },
        );
      }),
      http.put("https://fake-s3.test/*", () => {
        s3Put();
        return new HttpResponse(null, { status: 200 });
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
      expect.objectContaining({ batch_id: 9, bucket: "BREEDING" }),
    );
    await waitFor(() => {
      expect(s3Put).toHaveBeenCalledTimes(1);
    });
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
});
