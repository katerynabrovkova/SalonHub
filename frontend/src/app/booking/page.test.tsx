// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// notFound() throws in real Next.js to halt rendering — the mock must
// throw too so this test observes the same control-flow halt, not a
// silent fall-through.
vi.mock("next/navigation", () => ({
  notFound: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));

import BookingPage from "./page";

function searchParamsOf(params: Record<string, string | undefined>) {
  return Promise.resolve(params);
}

describe("BookingPage routing skeleton", () => {
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

  it("test_service_entry_step_omitted_with_service_renders_specialist_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "service", service: "5" }),
    });
    render(element);
    expect(screen.getByTestId("step-specialist")).toBeInTheDocument();
  });

  it("test_specialist_entry_step_omitted_with_specialist_renders_service_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist", specialist: "9" }),
    });
    render(element);
    expect(screen.getByTestId("step-service")).toBeInTheDocument();
  });

  it("test_service_entry_any_specialist_step_3_renders_datetime_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "service",
        service: "5",
        specialist: "any",
        step: "3",
      }),
    });
    render(element);
    expect(screen.getByTestId("step-datetime")).toBeInTheDocument();
  });

  it("test_service_entry_step_2_renders_specialist_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "service", service: "5", step: "2" }),
    });
    render(element);
    expect(screen.getByTestId("step-specialist")).toBeInTheDocument();
  });

  it("test_service_entry_step_3_renders_datetime_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "service",
        service: "5",
        specialist: "9",
        step: "3",
      }),
    });
    render(element);
    expect(screen.getByTestId("step-datetime")).toBeInTheDocument();
  });

  it("test_specialist_entry_step_2_renders_service_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist", specialist: "9", step: "2" }),
    });
    render(element);
    expect(screen.getByTestId("step-service")).toBeInTheDocument();
  });

  it("test_specialist_entry_step_3_renders_datetime_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({
        entry: "specialist",
        specialist: "9",
        service: "5",
        step: "3",
      }),
    });
    render(element);
    expect(screen.getByTestId("step-datetime")).toBeInTheDocument();
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

  it("test_step_beyond_3_renders_not_implemented_placeholder", async () => {
    const element = await BookingPage({
      searchParams: searchParamsOf({ entry: "service", service: "5", step: "4" }),
    });
    render(element);
    expect(screen.getByTestId("step-not-implemented")).toBeInTheDocument();
  });
});
