// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the data-fetch module boundary, not raw fetch — same convention as
// specialists/[id]/page.test.tsx and services/page.test.tsx.
vi.mock("@/lib/specialists/getSpecialistsPage", () => ({
  getSpecialistsPage: vi.fn(),
}));

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what page.tsx sees.
import { getSpecialistsPage, type Specialist } from "@/lib/specialists/getSpecialistsPage";
import { SALON_SLUG_HEADER } from "@/middleware";
import { headers } from "next/headers";

import SpecialistsPage from "./page";

const mockedHeaders = vi.mocked(headers);
const mockedGetSpecialistsPage = vi.mocked(getSpecialistsPage);

function searchParamsOf(params: Record<string, string | undefined>) {
  return Promise.resolve(params);
}

function specialist(overrides: Partial<Specialist> = {}): Specialist {
  return {
    id: 9,
    salon: 1,
    name: "Olena",
    bio: "Nail artist",
    photo: null,
    is_active: true,
    services: [{ id: 101, name: "Manicure" }],
    average_rating: null,
    review_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const SPECIALISTS = [specialist({ id: 9, name: "Olena" }), specialist({ id: 12, name: "Iryna" })];

beforeEach(() => {
  mockedHeaders.mockReset();
  mockedGetSpecialistsPage.mockReset();

  mockedHeaders.mockResolvedValue({
    get: (name: string) => (name === SALON_SLUG_HEADER ? "bella-demo" : null),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- minimal ReadonlyHeaders stand-in, only .get() is used by page.tsx
  } as any);
});

// docs/DECISIONS.md § "Fix: specialist list cards lead to the specialist
// page (decided 22.10.2026)": each grid card is one whole-card link to
// /specialists/<id>, named by the specialist, with nothing interactive
// nested inside it.
describe("SpecialistsPage", () => {
  it("test_each_card_links_to_its_specialist_page_named_by_the_specialist", async () => {
    mockedGetSpecialistsPage.mockResolvedValueOnce({
      specialists: SPECIALISTS,
      currentPage: 1,
      totalPages: 1,
    });

    render(await SpecialistsPage({ searchParams: searchParamsOf({}) }));

    expect(screen.getByRole("link", { name: /Olena/ })).toHaveAttribute("href", "/specialists/9");
    expect(screen.getByRole("link", { name: /Iryna/ })).toHaveAttribute("href", "/specialists/12");
    // The name is the specialist's name only, not the whole card text.
    expect(screen.getByRole("link", { name: /Olena/ })).toHaveAccessibleName("Olena");
    expect(screen.getByRole("link", { name: /Iryna/ })).toHaveAccessibleName("Iryna");
  });

  it("test_card_link_contains_no_other_link_or_button", async () => {
    mockedGetSpecialistsPage.mockResolvedValueOnce({
      specialists: SPECIALISTS,
      currentPage: 1,
      totalPages: 1,
    });

    render(await SpecialistsPage({ searchParams: searchParamsOf({}) }));

    // Asserted first so this test cannot pass vacuously while no card link
    // exists at all.
    const cardLinks = screen
      .getAllByRole("link")
      .filter((link) => link.getAttribute("href")?.startsWith("/specialists/"));
    expect(cardLinks).toHaveLength(SPECIALISTS.length);

    for (const cardLink of cardLinks) {
      expect(within(cardLink).queryAllByRole("link")).toHaveLength(0);
      expect(within(cardLink).queryAllByRole("button")).toHaveLength(0);
    }
  });

  it("test_pagination_links_still_render_when_there_are_more_pages", async () => {
    mockedGetSpecialistsPage.mockResolvedValueOnce({
      specialists: SPECIALISTS,
      currentPage: 2,
      totalPages: 3,
    });

    render(await SpecialistsPage({ searchParams: searchParamsOf({ page: "2" }) }));

    expect(screen.getByRole("link", { name: "← Назад" })).toHaveAttribute("href", "?page=1");
    expect(screen.getByRole("link", { name: "Далі →" })).toHaveAttribute("href", "?page=3");
  });
});
