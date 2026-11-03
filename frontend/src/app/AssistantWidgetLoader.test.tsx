// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock at the module boundaries, same convention as specialists/page.test.tsx:
// the slug header, the salon-info fetch and the client widget itself.
vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

vi.mock("@/lib/tenants/getSalonInfoPage", () => ({
  getSalonInfoPage: vi.fn(),
}));

vi.mock("./AssistantWidget", () => ({
  default: ({ salonName }: { salonName: string }) => (
    <div data-testid="assistant-widget">{salonName}</div>
  ),
}));

// Imported after the mocks above so the mocked modules are what the loader sees.
import { getSalonInfoPage } from "@/lib/tenants/getSalonInfoPage";
import { SALON_SLUG_HEADER } from "@/middleware";
import { headers } from "next/headers";

import AssistantWidgetLoader from "./AssistantWidgetLoader";

const mockedHeaders = vi.mocked(headers);
const mockedGetSalonInfoPage = vi.mocked(getSalonInfoPage);

function withSlug(slug: string | null) {
  mockedHeaders.mockResolvedValue({
    get: (name: string) => (name === SALON_SLUG_HEADER ? slug : null),
    // eslint-disable-next-line @typescript-eslint/no-explicit-any -- minimal ReadonlyHeaders stand-in, only .get() is used by the loader
  } as any);
}

function salonInfo(overrides: { name?: string; assistant_enabled?: boolean } = {}) {
  return {
    name: "Bella",
    currency: "UAH",
    assistant_enabled: true,
    ...overrides,
  };
}

async function renderLoader() {
  return render(<>{await AssistantWidgetLoader()}</>);
}

beforeEach(() => {
  mockedHeaders.mockReset();
  mockedGetSalonInfoPage.mockReset();
  withSlug("bella-demo");
});

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.3b: the loader renders
// nothing unless a real salon has the assistant on, so the root layout can
// never break other pages.
describe("AssistantWidgetLoader", () => {
  it("test_null_slug_renders_nothing_and_fetches_nothing", async () => {
    withSlug(null);

    const { container } = await renderLoader();

    expect(container).toBeEmptyDOMElement();
    expect(mockedGetSalonInfoPage).not.toHaveBeenCalled();
  });

  it("test_assistant_disabled_renders_nothing", async () => {
    mockedGetSalonInfoPage.mockResolvedValueOnce(salonInfo({ assistant_enabled: false }));

    const { container } = await renderLoader();

    expect(container).toBeEmptyDOMElement();
  });

  it("test_failed_fetch_renders_nothing_and_does_not_throw", async () => {
    mockedGetSalonInfoPage.mockRejectedValueOnce(new Error("Failed to fetch salon info"));

    const { container } = await renderLoader();

    expect(container).toBeEmptyDOMElement();
  });

  it("test_enabled_renders_the_widget_with_the_salon_name", async () => {
    mockedGetSalonInfoPage.mockResolvedValueOnce(salonInfo({ name: "Bella" }));

    await renderLoader();

    expect(screen.getByTestId("assistant-widget")).toHaveTextContent("Bella");
  });

  it("test_fetches_salon_info_for_the_slug_in_ukrainian", async () => {
    mockedGetSalonInfoPage.mockResolvedValueOnce(salonInfo());

    await renderLoader();

    expect(mockedGetSalonInfoPage).toHaveBeenCalledWith("bella-demo", "uk");
  });
});
