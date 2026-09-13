// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the two data-fetch module boundaries, not raw fetch — same
// convention as login/page.test.tsx (mock the seam, not fetch itself).
vi.mock("@/lib/catalog/getServiceCategories", () => ({
  getServiceCategories: vi.fn(),
}));

vi.mock("@/lib/catalog/getServicesPage", () => ({
  getServicesPage: vi.fn(),
}));

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

// Imported after the mocks above so the mocked modules are what page.tsx sees.
import { getServiceCategories } from "@/lib/catalog/getServiceCategories";
import { getServicesPage } from "@/lib/catalog/getServicesPage";
import { SALON_SLUG_HEADER } from "@/middleware";
import { headers } from "next/headers";

import ServicesPage from "./page";

const mockedHeaders = vi.mocked(headers);
const mockedGetServiceCategories = vi.mocked(getServiceCategories);
const mockedGetServicesPage = vi.mocked(getServicesPage);

function searchParamsOf(params: Record<string, string | undefined>) {
  return Promise.resolve(params);
}

beforeEach(() => {
  mockedHeaders.mockReset();
  mockedGetServiceCategories.mockReset();
  mockedGetServicesPage.mockReset();

  mockedHeaders.mockResolvedValue({
    get: (name: string) => (name === SALON_SLUG_HEADER ? "bella-demo" : null),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- minimal ReadonlyHeaders stand-in, only .get() is used by page.tsx
  } as any);
});

describe("ServicesPage: category-list / service-grid split", () => {
  it("test_no_category_param_renders_category_grid", async () => {
    mockedGetServiceCategories.mockResolvedValueOnce([{ id: 1, name: "Nails", photo: null }]);

    const element = await ServicesPage({ searchParams: searchParamsOf({}) });
    render(element);

    expect(mockedGetServiceCategories).toHaveBeenCalledWith("bella-demo");
    expect(mockedGetServicesPage).not.toHaveBeenCalled();
    expect(screen.getByText("Nails")).toBeInTheDocument();
  });

  it("test_category_with_photo_renders_image", async () => {
    mockedGetServiceCategories.mockResolvedValueOnce([
      { id: 1, name: "Hair", photo: "https://example.com/hair.jpg" },
    ]);

    const element = await ServicesPage({ searchParams: searchParamsOf({}) });
    render(element);

    const img = screen.getByRole("img", { name: "Hair" });
    expect(img).toHaveAttribute("src", "https://example.com/hair.jpg");
  });

  it("test_category_without_photo_renders_initials_fallback", async () => {
    mockedGetServiceCategories.mockResolvedValueOnce([{ id: 1, name: "Sugaring", photo: null }]);

    const element = await ServicesPage({ searchParams: searchParamsOf({}) });
    render(element);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    // Mirrors specialists/page.tsx's initials() helper: first letter of the
    // (single) word, uppercased.
    expect(screen.getByText("S")).toBeInTheDocument();
  });

  it("test_category_card_links_to_services_with_category_param", async () => {
    mockedGetServiceCategories.mockResolvedValueOnce([{ id: 7, name: "Brows", photo: null }]);

    const element = await ServicesPage({ searchParams: searchParamsOf({}) });
    render(element);

    const link = screen.getByRole("link", { name: /Brows/i });
    expect(link).toHaveAttribute("href", "/services?category=7");
  });

  it("test_category_param_present_renders_service_grid", async () => {
    mockedGetServicesPage.mockResolvedValueOnce({
      services: [
        {
          id: 1,
          salon: 1,
          category: { id: 1, name: "Nails" },
          name: "Manicure",
          duration_minutes: 60,
          price: "500.00",
          buffer_minutes: 15,
          ordering: 0,
          is_active: true,
          created_at: "",
          updated_at: "",
        },
      ],
      currentPage: 1,
      totalPages: 1,
    });

    const element = await ServicesPage({ searchParams: searchParamsOf({ category: "1" }) });
    render(element);

    // Assumed planned signature: getServicesPage(slug, page, category) — a
    // minimal extension of the current (slug, page=1) so the backend's
    // existing ?category= filter (backend/catalog/views.py) is reached.
    // Adjust this assertion if the real implementation shapes the call
    // differently (e.g. an options object).
    expect(mockedGetServicesPage).toHaveBeenCalledWith("bella-demo", 1, "1");
    expect(mockedGetServiceCategories).not.toHaveBeenCalled();
    expect(screen.getByText("Manicure")).toBeInTheDocument();
  });

  it("test_pagination_links_forward_category_param", async () => {
    mockedGetServicesPage.mockResolvedValueOnce({
      services: [
        {
          id: 1,
          salon: 1,
          category: { id: 1, name: "Nails" },
          name: "Manicure",
          duration_minutes: 60,
          price: "500.00",
          buffer_minutes: 15,
          ordering: 0,
          is_active: true,
          created_at: "",
          updated_at: "",
        },
      ],
      currentPage: 1,
      totalPages: 2,
    });

    const element = await ServicesPage({
      searchParams: searchParamsOf({ category: "1" }),
    });
    render(element);

    // Regression guard: browsing page 2+ of a filtered category must not
    // silently drop back to the unfiltered service list.
    const nextLink = screen.getByRole("link", { name: /Далі/i });
    expect(nextLink).toHaveAttribute("href", "?page=2&category=1");
  });
});
