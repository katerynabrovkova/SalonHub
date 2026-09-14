// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
import { getServicesPage, type Service } from "@/lib/catalog/getServicesPage";
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

// Red phase (docs/DECISIONS.md § Stage 13 reopened: implementation
// decisions for the popup, category fetch, and detail-page removal).
// None of this is implemented yet: the card is still the old
// whole-card <Link> to the now-deleted /services/[id] route, and no "i"
// button or ServiceInfoPopover exists. These 6 tests are expected to
// fail until that restructure + the new component land.
//
// `description` is not yet on the `Service` interface (planned as a
// type-only addition, no test needed for it) — extended locally here so
// the fixture can carry it; a `ServiceWithDescription[]` is still
// structurally assignable to `Service[]` since it's a superset.
type ServiceWithDescription = Service & { description: string | null };

function serviceFixture(overrides: Partial<ServiceWithDescription> = {}): ServiceWithDescription {
  return {
    id: 1,
    salon: 1,
    category: { id: 1, name: "Nails" },
    name: "Manicure",
    description: "A classic manicure with polish.",
    duration_minutes: 60,
    price: "500.00",
    buffer_minutes: 15,
    ordering: 0,
    is_active: true,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

async function renderServiceGrid(service: ServiceWithDescription) {
  mockedGetServicesPage.mockResolvedValueOnce({
    services: [service],
    currentPage: 1,
    totalPages: 1,
  });

  const element = await ServicesPage({ searchParams: searchParamsOf({ category: "1" }) });
  return render(element);
}

describe("ServicesPage: service card restructure + info popover (not yet implemented)", () => {
  it("test_service_card_links_to_booking_with_service_param", async () => {
    await renderServiceGrid(serviceFixture({ id: 42, name: "Manicure" }));

    // The stretched link has no visible text of its own (it's an
    // absolutely-positioned, full-card click target) — it's given an
    // aria-label matching the service name so it's still an accessible
    // link, distinct from the visible <h2> heading with the same text.
    const link = screen.getByRole("link", { name: "Manicure" });
    expect(link).toHaveAttribute("href", "/booking?entry=service&service=42");
  });

  it("test_info_button_is_not_nested_inside_link", async () => {
    const { container } = await renderServiceGrid(serviceFixture({ name: "Manicure" }));

    const link = container.querySelector("a");
    const infoButton = screen.getByRole("button", { name: /інформац/i });

    expect(link).not.toBeNull();
    // Regression guard: a <button> nested inside an <a> is invalid HTML
    // and produces unpredictable click behavior — checked via actual DOM
    // containment, not just visual/z-index position.
    expect(link?.contains(infoButton)).toBe(false);
  });

  it("test_info_button_renders_inline_with_service_name", async () => {
    await renderServiceGrid(serviceFixture({ name: "Manicure" }));

    const infoButton = screen.getByRole("button", { name: /інформац/i });
    const heading = screen.getByRole("heading", { name: "Manicure" });

    // The "i" button sits in the same header row as the service name —
    // not down in the duration/price block below.
    expect(infoButton.parentElement).toBe(heading.parentElement);
  });

  it("test_info_button_opens_popover", async () => {
    const user = userEvent.setup();
    await renderServiceGrid(serviceFixture({ name: "Manicure" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /інформац/i }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("test_popover_shows_service_details", async () => {
    const user = userEvent.setup();
    await renderServiceGrid(
      serviceFixture({
        name: "Manicure",
        description: "A classic manicure with polish.",
        duration_minutes: 60,
        price: "500.00",
        category: { id: 1, name: "Nails" },
      }),
    );

    await user.click(screen.getByRole("button", { name: /інформац/i }));
    const dialog = await screen.findByRole("dialog");

    expect(dialog).toHaveTextContent("Manicure");
    expect(dialog).toHaveTextContent("A classic manicure with polish.");
    expect(dialog).toHaveTextContent("60");
    expect(dialog).toHaveTextContent("500.00");
    expect(dialog).toHaveTextContent("Nails");
  });

  it("test_popover_handles_null_description", async () => {
    const user = userEvent.setup();
    await renderServiceGrid(serviceFixture({ name: "Manicure", description: null }));

    await user.click(screen.getByRole("button", { name: /інформац/i }));
    const dialog = await screen.findByRole("dialog");

    expect(dialog).toBeInTheDocument();
    expect(dialog).not.toHaveTextContent("null");
  });

  it("test_backdrop_click_closes_popover", async () => {
    const user = userEvent.setup();
    await renderServiceGrid(serviceFixture({ name: "Manicure" }));

    await user.click(screen.getByRole("button", { name: /інформац/i }));
    const dialog = await screen.findByRole("dialog");

    // A click that lands on the <dialog> element itself (not on anything
    // inside it) is a ::backdrop click — showModal() gives focus-trap and
    // Escape-to-close for free, but not click-outside, so this is the one
    // close path that needs its own explicit handler.
    await user.click(dialog);

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
