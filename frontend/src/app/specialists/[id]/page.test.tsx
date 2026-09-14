// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// notFound() throws in real Next.js to halt rendering — the mock must throw
// too so this test observes the same control-flow halt, not a silent
// fall-through. Same convention as booking/page.test.tsx.
vi.mock("next/navigation", () => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));

// Mock the data-fetch module boundary, not raw fetch — same convention as
// services/page.test.tsx (mock the seam, not fetch itself).
vi.mock("@/lib/specialists/getSpecialistDetailPage", () => ({
  getSpecialistDetailPage: vi.fn(),
}));

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what page.tsx sees.
import {
  getSpecialistDetailPage,
  type SpecialistDetail,
} from "@/lib/specialists/getSpecialistDetailPage";
import { SALON_SLUG_HEADER } from "@/middleware";
import { headers } from "next/headers";

import SpecialistDetailPage from "./page";

const mockedHeaders = vi.mocked(headers);
const mockedGetSpecialistDetailPage = vi.mocked(getSpecialistDetailPage);

function paramsOf(id: string) {
  return Promise.resolve({ id });
}

function specialist(overrides: Partial<SpecialistDetail> = {}): SpecialistDetail {
  return {
    id: 7,
    salon: 1,
    name: "Olena",
    bio: "Nail artist",
    photo: "https://example.com/photos/olena.jpg",
    is_active: true,
    services: [{ id: 10, name: "Manicure" }],
    average_rating: 4.5,
    review_count: 2,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  mockedHeaders.mockReset();
  mockedGetSpecialistDetailPage.mockReset();

  mockedHeaders.mockResolvedValue({
    get: (name: string) => (name === SALON_SLUG_HEADER ? "bella-demo" : null),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- minimal ReadonlyHeaders stand-in, only .get() is used by page.tsx
  } as any);
});

describe("SpecialistDetailPage", () => {
  it("test_invalid_id_calls_not_found", async () => {
    await expect(
      SpecialistDetailPage({ params: paramsOf("not-a-number") }),
    ).rejects.toThrow("NEXT_NOT_FOUND");

    expect(mockedGetSpecialistDetailPage).not.toHaveBeenCalled();
  });

  it("test_null_specialist_calls_not_found", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(null);

    await expect(SpecialistDetailPage({ params: paramsOf("7") })).rejects.toThrow(
      "NEXT_NOT_FOUND",
    );
  });

  it("test_renders_specialist_with_photo", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialist());

    const element = await SpecialistDetailPage({ params: paramsOf("7") });
    render(element);

    expect(screen.getByRole("img", { name: "Olena" })).toBeInTheDocument();
  });

  it("test_renders_specialist_without_photo", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialist({ photo: null }));

    const element = await SpecialistDetailPage({ params: paramsOf("7") });
    render(element);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("O")).toBeInTheDocument();
  });

  it("test_renders_bio_when_present", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialist({ bio: "Nail artist" }));

    const element = await SpecialistDetailPage({ params: paramsOf("7") });
    render(element);

    expect(screen.getByText("Nail artist")).toBeInTheDocument();
  });

  it("test_omits_bio_when_empty", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialist({ bio: "" }));

    const element = await SpecialistDetailPage({ params: paramsOf("7") });
    render(element);

    expect(screen.queryByText("Nail artist")).not.toBeInTheDocument();
  });

  it("test_rating_line_with_reviews", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(
      specialist({ average_rating: 4.5, review_count: 2 }),
    );

    const element = await SpecialistDetailPage({ params: paramsOf("7") });
    render(element);

    expect(screen.getByText("★ 4.5 (2 відгуків)")).toBeInTheDocument();
  });

  it("test_rating_line_without_reviews", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(
      specialist({ average_rating: null, review_count: 0 }),
    );

    const element = await SpecialistDetailPage({ params: paramsOf("7") });
    render(element);

    expect(screen.getByText("Немає відгуків")).toBeInTheDocument();
  });

  it("test_book_link_has_correct_href", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialist({ id: 7 }));

    const element = await SpecialistDetailPage({ params: paramsOf("7") });
    render(element);

    expect(screen.getByRole("link", { name: /заброн/i })).toHaveAttribute(
      "href",
      "/booking?entry=specialist&specialist=7&step=2",
    );
  });
});
