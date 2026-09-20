// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// notFound() throws in real Next.js to halt rendering — the mock must
// throw too so this test observes the same control-flow halt, not a
// silent fall-through. useRouter's push is mocked the same way
// services/page.test.tsx mocks it, since the generalized
// ServiceSelectionGrid (rendered by the entry=specialist step 2 branch)
// calls useRouter() internally.
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
  useRouter: () => ({ push: pushMock }),
}));

// Mock the data-fetch module boundaries, not raw fetch — same convention as
// specialists/[id]/page.test.tsx and specialists/page.test.tsx.
vi.mock("@/lib/catalog/getServiceDetailPage", () => ({
  getServiceDetailPage: vi.fn(),
}));

vi.mock("@/lib/specialists/getSpecialistDetailPage", () => ({
  getSpecialistDetailPage: vi.fn(),
}));

vi.mock("@/lib/specialists/getSpecialistsPage", () => ({
  getSpecialistsPage: vi.fn(),
}));

vi.mock("@/lib/scheduling/getAvailability", () => ({
  getAvailability: vi.fn(),
}));

vi.mock("@/lib/tenants/getSalonInfoPage", () => ({
  getSalonInfoPage: vi.fn(),
}));

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

// BookingStep4 (rendered by step 4) calls useAuth() itself — same mock
// convention as client/layout.test.tsx.
const mockedUseAuth = vi.fn();
vi.mock("@/app/AuthContext", () => ({
  useAuth: () => mockedUseAuth(),
}));

// Imported after the mocks above so the mocked modules are what page.tsx sees.
import { getServiceDetailPage } from "@/lib/catalog/getServiceDetailPage";
import { formatPrice } from "@/lib/pricing/formatPrice";
import { getAvailability } from "@/lib/scheduling/getAvailability";
import {
  getSpecialistDetailPage,
  type SpecialistDetail,
} from "@/lib/specialists/getSpecialistDetailPage";
import { getSpecialistsPage, type Specialist } from "@/lib/specialists/getSpecialistsPage";
import { getSalonInfoPage } from "@/lib/tenants/getSalonInfoPage";
import { SALON_SLUG_HEADER } from "@/middleware";
import { headers } from "next/headers";

import { BookingContactInfoProvider } from "./BookingContactInfoContext";
import BookingPage from "./page";

const mockedHeaders = vi.mocked(headers);
const mockedGetServiceDetailPage = vi.mocked(getServiceDetailPage);
const mockedGetSpecialistDetailPage = vi.mocked(getSpecialistDetailPage);
const mockedGetSpecialistsPage = vi.mocked(getSpecialistsPage);
const mockedGetAvailability = vi.mocked(getAvailability);
const mockedGetSalonInfoPage = vi.mocked(getSalonInfoPage);

// formatPrice's Intl.NumberFormat output contains a literal non-breaking
// space (U+00A0). getByText's default normalizer collapses whitespace only
// in the DOM's own text before comparing, not in a literal string matcher —
// so an exact-string query would mismatch against the (nbsp-collapsed)
// rendered text despite them being visually identical. A regex sidesteps
// this: `\s` matches nbsp on the DOM side, and there's no separate
// normalization step on the matcher side to go wrong.
function formattedPriceRegex(price: string, currency: string): RegExp {
  const escaped = formatPrice(price, currency).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(escaped.replace(/\s+/g, "\\s+"));
}

function searchParamsOf(params: Record<string, string | undefined>) {
  return Promise.resolve(params);
}

function specialistListFixture(overrides: Partial<Specialist> = {}): Specialist {
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

function mockSlug(slug: string | null) {
  mockedHeaders.mockResolvedValueOnce({
    get: (name: string) => (name === SALON_SLUG_HEADER ? slug : null),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- minimal ReadonlyHeaders stand-in, only .get() is used by page.tsx
  } as any);
}

describe("BookingPage routing skeleton", () => {
  beforeEach(() => {
    mockedGetServiceDetailPage.mockReset();
    mockedUseAuth.mockReset();
    mockedUseAuth.mockReturnValue({ me: null, loading: false, login: vi.fn(), logout: vi.fn() });
  });

  it("test_explicit_step_1_calls_not_found", async () => {
    await expect(
      BookingPage({
        searchParams: searchParamsOf({ entry: "service", service: "5", step: "1" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");

    await expect(
      BookingPage({
        searchParams: searchParamsOf({ entry: "specialist", specialist: "9", step: "1" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_service_entry_step_omitted_with_service_renders_specialist_selection", async () => {
    mockSlug("bella-demo");
    mockedGetSpecialistsPage.mockResolvedValueOnce({
      specialists: [specialistListFixture()],
      currentPage: 1,
      totalPages: 1,
    });

    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "service", service: "5" }),
    });
    render(element);

    expect(mockedGetSpecialistsPage).toHaveBeenCalledWith("bella-demo", 1, "5");
    expect(screen.getByRole("radio", { name: "Будь-який спеціаліст" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Olena" })).toBeInTheDocument();
  });

  it("test_service_entry_any_specialist_step_3_renders_datetime_grid", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    mockSlug("bella-demo");
    mockedGetAvailability.mockResolvedValueOnce({
      availableTimes: ["2026-08-17T09:00:00+03:00"],
    });

    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "service",
        service: "5",
        specialist: "any",
        step: "3",
      }),
    });
    render(element);
    vi.useRealTimers();

    expect(mockedGetAvailability).toHaveBeenCalledWith(
      "bella-demo",
      "5",
      "2026-08-17",
      "2026-08-30",
      undefined,
    );
    expect(screen.getByRole("button", { name: "2026-08-17" })).toBeEnabled();
    expect(screen.getByRole("link", { name: /далі/i })).toBeInTheDocument();
  });

  it("test_service_entry_step_2_renders_specialist_selection", async () => {
    mockSlug("bella-demo");
    mockedGetSpecialistsPage.mockResolvedValueOnce({
      specialists: [specialistListFixture()],
      currentPage: 1,
      totalPages: 1,
    });

    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "service", service: "5", step: "2" }),
    });
    render(element);

    expect(mockedGetSpecialistsPage).toHaveBeenCalledWith("bella-demo", 1, "5");
    expect(screen.getByRole("radio", { name: "Будь-який спеціаліст" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Olena" })).toBeInTheDocument();
  });

  it("test_service_entry_step_3_renders_datetime_grid", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    mockSlug("bella-demo");
    mockedGetAvailability.mockResolvedValueOnce({ availableTimes: [] });

    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "service",
        service: "5",
        specialist: "9",
        step: "3",
      }),
    });
    render(element);
    vi.useRealTimers();

    expect(mockedGetAvailability).toHaveBeenCalledWith(
      "bella-demo",
      "5",
      "2026-08-17",
      "2026-08-30",
      "9",
    );
    expect(screen.getByRole("link", { name: /далі/i })).toBeInTheDocument();
  });

  it("test_specialist_entry_step_3_renders_datetime_grid", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    mockSlug("bella-demo");
    mockedGetAvailability.mockResolvedValueOnce({ availableTimes: [] });

    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "specialist",
        specialist: "9",
        service: "5",
        step: "3",
      }),
    });
    render(element);
    vi.useRealTimers();

    expect(mockedGetAvailability).toHaveBeenCalledWith(
      "bella-demo",
      "5",
      "2026-08-17",
      "2026-08-30",
      "9",
    );
    expect(screen.getByRole("link", { name: /далі/i })).toBeInTheDocument();
  });

  it("test_invalid_entry_calls_not_found", async () => {
    await expect(
      BookingPage({ searchParams: searchParamsOf({ entry: "bogus" }) }),
    ).rejects.toThrow("NEXT_NOT_FOUND");

    await expect(
      BookingPage({ searchParams: searchParamsOf({}) }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_service_entry_step_2_without_service_calls_not_found", async () => {
    await expect(
      BookingPage({ searchParams: searchParamsOf({ entry: "service", step: "2" }) }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_service_entry_step_3_without_specialist_calls_not_found", async () => {
    await expect(
      BookingPage({
        searchParams: searchParamsOf({ entry: "service", service: "5", step: "3" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_specialist_entry_step_2_without_specialist_calls_not_found", async () => {
    await expect(
      BookingPage({ searchParams: searchParamsOf({ entry: "specialist", step: "2" }) }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_specialist_entry_step_3_without_service_calls_not_found", async () => {
    await expect(
      BookingPage({
        searchParams: searchParamsOf({ entry: "specialist", specialist: "9", step: "3" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_step3_non_date_date_from_calls_not_found", async () => {
    mockSlug("bella-demo");

    await expect(
      BookingPage({
        searchParams: searchParamsOf({
          entry: "service",
          service: "5",
          specialist: "any",
          step: "3",
          date_from: "abc",
        }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    expect(mockedGetAvailability).not.toHaveBeenCalled();
  });

  it("test_step3_unreal_calendar_date_from_calls_not_found", async () => {
    mockSlug("bella-demo");

    // 2026-02-30 doesn't exist -- Date.UTC would silently normalize it to
    // 2026-03-02 rather than rejecting it, which is exactly the bug this
    // round-trip check exists to catch.
    await expect(
      BookingPage({
        searchParams: searchParamsOf({
          entry: "service",
          service: "5",
          specialist: "any",
          step: "3",
          date_from: "2026-02-30",
        }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    expect(mockedGetAvailability).not.toHaveBeenCalled();
  });

  it("test_step3_no_date_from_renders_with_no_previous_window_link", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    mockSlug("bella-demo");
    mockedGetAvailability.mockResolvedValueOnce({ availableTimes: [] });

    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "service", service: "5", specialist: "any", step: "3" }),
    });
    render(element);
    vi.useRealTimers();

    expect(screen.getByRole("link", { name: /далі/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /назад/i })).not.toBeInTheDocument();
  });

  it("test_step3_far_future_date_from_renders_a_clamped_previous_window_link", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-17T12:00:00Z"));
    mockSlug("bella-demo");
    mockedGetAvailability.mockResolvedValueOnce({ availableTimes: [] });

    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "service",
        service: "5",
        specialist: "any",
        step: "3",
        date_from: "2099-01-01",
      }),
    });
    render(element);
    vi.useRealTimers();

    expect(screen.getByRole("link", { name: /назад/i })).toHaveAttribute(
      "href",
      "/booking?entry=service&service=5&specialist=any&step=3&date_from=2098-12-18",
    );
  });

  it("test_step_beyond_4_renders_not_implemented_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "service", service: "5", step: "5" }),
    });
    render(element);
    expect(screen.getByTestId("step-not-implemented")).toBeInTheDocument();
  });

  it("test_step4_renders_contact_info_form_with_decoded_slot", async () => {
    mockSlug("bella-demo");
    mockedGetServiceDetailPage.mockResolvedValueOnce({
      id: 5,
      name: "Manicure",
      duration_minutes: 60,
    });

    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "service",
        service: "5",
        specialist: "any",
        step: "4",
        slot: encodeURIComponent("2026-08-17T09:00:00+03:00"),
      }),
    });
    render(<BookingContactInfoProvider>{element}</BookingContactInfoProvider>);

    expect(mockedGetServiceDetailPage).toHaveBeenCalledWith("bella-demo", 5);
    expect(mockedGetSpecialistDetailPage).not.toHaveBeenCalled();
    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(screen.getByText("Будь-який спеціаліст")).toBeInTheDocument();
    expect(screen.getByLabelText("Ім'я")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Телефон")).toBeInTheDocument();
  });

  it("test_step4_fetches_specialist_when_not_any_and_renders_its_name", async () => {
    mockSlug("bella-demo");
    mockedGetServiceDetailPage.mockResolvedValueOnce({
      id: 5,
      name: "Manicure",
      duration_minutes: 60,
    });
    mockedGetSpecialistDetailPage.mockResolvedValueOnce({
      id: 9,
      salon: 1,
      name: "Olena",
      bio: "",
      photo: null,
      is_active: true,
      services: [],
      services_detail: [],
      average_rating: null,
      review_count: 0,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "service",
        service: "5",
        specialist: "9",
        step: "4",
        slot: encodeURIComponent("2026-08-17T09:00:00+03:00"),
      }),
    });
    render(<BookingContactInfoProvider>{element}</BookingContactInfoProvider>);

    expect(mockedGetSpecialistDetailPage).toHaveBeenCalledWith("bella-demo", 9);
    expect(screen.getByText("Olena")).toBeInTheDocument();
  });

  it("test_step4_null_service_calls_not_found", async () => {
    mockSlug("bella-demo");
    mockedGetServiceDetailPage.mockResolvedValueOnce(null);

    await expect(
      BookingPage({
        searchParams: searchParamsOf({
          entry: "service",
          service: "5",
          specialist: "any",
          step: "4",
          slot: encodeURIComponent("2026-08-17T09:00:00+03:00"),
        }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_step4_null_specialist_calls_not_found", async () => {
    mockSlug("bella-demo");
    mockedGetServiceDetailPage.mockResolvedValueOnce({
      id: 5,
      name: "Manicure",
      duration_minutes: 60,
    });
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(null);

    await expect(
      BookingPage({
        searchParams: searchParamsOf({
          entry: "service",
          service: "5",
          specialist: "9",
          step: "4",
          slot: encodeURIComponent("2026-08-17T09:00:00+03:00"),
        }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_step4_malformed_percent_sequence_in_slot_calls_not_found", async () => {
    mockSlug("bella-demo");

    await expect(
      BookingPage({
        searchParams: searchParamsOf({
          entry: "service",
          service: "5",
          specialist: "any",
          step: "4",
          // A literal trailing "%" is not a valid percent-escape --
          // decodeURIComponent throws a URIError on this.
          slot: "2026-08-17T09%3A00%3A00+03:00%",
        }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    expect(mockedGetServiceDetailPage).not.toHaveBeenCalled();
  });

  it("test_step4_slot_missing_offset_calls_not_found", async () => {
    mockSlug("bella-demo");

    await expect(
      BookingPage({
        searchParams: searchParamsOf({
          entry: "service",
          service: "5",
          specialist: "any",
          step: "4",
          // Decodes fine, but has no UTC offset -- an ambiguous local time,
          // not the shape this flow requires.
          slot: encodeURIComponent("2026-08-17T09:00:00"),
        }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
    expect(mockedGetServiceDetailPage).not.toHaveBeenCalled();
  });

  it("test_step4_null_slug_shows_platform_message", async () => {
    mockSlug(null);

    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "service",
        service: "5",
        specialist: "any",
        step: "4",
        slot: encodeURIComponent("2026-08-17T09:00:00+03:00"),
      }),
    });
    render(element);

    expect(screen.getByText("The platform is still in development.")).toBeInTheDocument();
  });

  it("test_step4_without_specialist_calls_not_found", async () => {
    await expect(
      BookingPage({
        searchParams: searchParamsOf({
          entry: "service",
          service: "5",
          step: "4",
          slot: encodeURIComponent("2026-08-17T09:00:00+03:00"),
        }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_step4_without_slot_calls_not_found", async () => {
    await expect(
      BookingPage({
        searchParams: searchParamsOf({
          entry: "service",
          service: "5",
          specialist: "any",
          step: "4",
        }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });
});

// Red phase (docs/DECISIONS.md § Stage 14 implementation decisions). None
// of this is implemented yet: booking/page.tsx's step 2 for
// entry=specialist is still the bare `<div data-testid="step-service" />`
// placeholder — it calls neither headers() nor getSpecialistDetailPage, and
// ServiceSelectionGrid doesn't yet accept a confirmTarget prop. These 5
// tests are expected to fail until that implementation lands.
type SpecialistDetailWithServicesDetail = SpecialistDetail & {
  services_detail: {
    id: number;
    name: string;
    duration_minutes: number;
    price: string;
    description: string | null;
  }[];
};

function specialistFixture(
  overrides: Partial<SpecialistDetailWithServicesDetail> = {},
): SpecialistDetailWithServicesDetail {
  return {
    id: 9,
    salon: 1,
    name: "Olena",
    bio: "Nail artist",
    photo: null,
    is_active: true,
    services: [{ id: 101, name: "Manicure" }],
    services_detail: [
      {
        id: 101,
        name: "Manicure",
        duration_minutes: 45,
        price: "350.00",
        description: "A relaxing hand treatment.",
      },
      {
        id: 102,
        name: "Pedicure",
        duration_minutes: 60,
        price: "500.00",
        description: null,
      },
    ],
    average_rating: null,
    review_count: 0,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("BookingPage step 2, entry=specialist: fetches and renders services_detail", () => {
  beforeEach(() => {
    pushMock.mockReset();
    mockedHeaders.mockReset();
    mockedGetSpecialistDetailPage.mockReset();
    mockedGetSalonInfoPage.mockReset();
    mockedGetSalonInfoPage.mockResolvedValue({ currency: "UAH" });

    mockedHeaders.mockResolvedValue({
      get: (name: string) => (name === SALON_SLUG_HEADER ? "bella-demo" : null),
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- minimal ReadonlyHeaders stand-in, only .get() is used by page.tsx
    } as any);
  });

  it("test_step2_specialist_null_slug_shows_platform_message", async () => {
    mockedHeaders.mockResolvedValue({
      get: () => null,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any -- minimal ReadonlyHeaders stand-in, only .get() is used by page.tsx
    } as any);

    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist", specialist: "9" }),
    });
    render(element);

    expect(screen.getByText("The platform is still in development.")).toBeInTheDocument();
    expect(mockedGetSpecialistDetailPage).not.toHaveBeenCalled();
  });

  it("test_step2_specialist_null_specialist_calls_not_found", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(null);

    await expect(
      BookingPage({
        searchParams: searchParamsOf({ entry: "specialist", specialist: "9" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });

  it("test_step2_specialist_malformed_id_calls_not_found", async () => {
    await expect(
      BookingPage({
        searchParams: searchParamsOf({ entry: "specialist", specialist: "not-a-number" }),
      }),
    ).rejects.toThrow("NEXT_NOT_FOUND");

    expect(mockedGetSpecialistDetailPage).not.toHaveBeenCalled();
  });

  it("test_step2_specialist_fetches_and_renders_services", async () => {
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialistFixture());

    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist", specialist: "9" }),
    });
    render(element);

    expect(mockedGetSpecialistDetailPage).toHaveBeenCalledWith("bella-demo", 9);
    expect(mockedGetSalonInfoPage).toHaveBeenCalledWith("bella-demo");
    expect(screen.getByRole("radio", { name: "Manicure" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Pedicure" })).toBeInTheDocument();
    expect(screen.getByText("45 хв")).toBeInTheDocument();
    // beforeEach stubs getSalonInfoPage to resolve { currency: "UAH" } by
    // default — the price is now the formatted string, not the bare value.
    expect(screen.getByText(formattedPriceRegex("350.00", "UAH"))).toBeInTheDocument();
    expect(screen.getByText("60 хв")).toBeInTheDocument();
    expect(screen.getByText(formattedPriceRegex("500.00", "UAH"))).toBeInTheDocument();
  });

  it("test_step2_specialist_fetches_salon_info_and_passes_currency_to_price_display", async () => {
    // currency=USD, distinct from the describe block's default UAH stub, so
    // a passing rendered-price assertion proves the prop actually flows
    // from getSalonInfoPage's result through to the grid, not just that
    // *some* formatted string happens to appear.
    mockedGetSalonInfoPage.mockResolvedValueOnce({ currency: "USD" });
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialistFixture());

    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist", specialist: "9" }),
    });
    render(element);

    expect(mockedGetSalonInfoPage).toHaveBeenCalledWith("bella-demo");
    expect(screen.getByText(formattedPriceRegex("350.00", "USD"))).toBeInTheDocument();
  });

  it("test_step2_specialist_confirm_navigates_to_step3", async () => {
    const user = userEvent.setup();
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialistFixture());

    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist", specialist: "9" }),
    });
    render(element);

    await user.click(screen.getByRole("radio", { name: "Manicure" }));
    await user.click(screen.getByRole("button", { name: /продовж/i }));

    expect(pushMock).toHaveBeenCalledWith(
      "/booking?entry=specialist&specialist=9&service=101&step=3",
    );
  });

  it("test_step2_specialist_info_button_shows_description", async () => {
    const user = userEvent.setup();
    mockedGetSpecialistDetailPage.mockResolvedValueOnce(specialistFixture());

    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist", specialist: "9" }),
    });
    render(element);

    const manicureRow = screen.getByRole("radio", { name: "Manicure" }).closest("div");
    const infoButton = manicureRow?.querySelector('[aria-label="Інформація про послугу"]');
    expect(infoButton).not.toBeNull();

    await user.click(infoButton as HTMLElement);
    const dialog = await screen.findByRole("dialog");

    // Proves the generalized ServiceInfoPopover works with no `category`
    // present on the services_detail shape — must not crash or render
    // anything broken (e.g. "undefined") in place of the missing category.
    expect(dialog).toHaveTextContent("A relaxing hand treatment.");
    expect(dialog).not.toHaveTextContent("undefined");
  });
});
