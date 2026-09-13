// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// notFound() throws in real Next.js to halt rendering (see
// src/app/services/[id]/page.tsx's use of it) — the mock must throw too so
// this test observes the same control-flow halt, not a silent fall-through.
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
  it("test_service_entry_step_1_or_omitted_renders_service_placeholder", async () => {
    const withStep = await BookingPage({
      searchParams: searchParamsOf({ entry: "service", step: "1" }),
    });
    const first = render(withStep);
    expect(first.getByTestId("step-service")).toBeInTheDocument();
    first.unmount();

    const stepOmitted = await BookingPage({
      searchParams: searchParamsOf({ entry: "service" }),
    });
    const second = render(stepOmitted);
    expect(second.getByTestId("step-service")).toBeInTheDocument();
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

  it("test_specialist_entry_step_1_or_omitted_renders_specialist_placeholder", async () => {
    const withStep = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist", step: "1" }),
    });
    const first = render(withStep);
    expect(first.getByTestId("step-specialist")).toBeInTheDocument();
    first.unmount();

    const stepOmitted = await BookingPage({
      searchParams: searchParamsOf({ entry: "specialist" }),
    });
    const second = render(stepOmitted);
    expect(second.getByTestId("step-specialist")).toBeInTheDocument();
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
      searchParams: searchParamsOf({ entry: "service", step: "4" }),
    });
    render(element);
    expect(screen.getByTestId("step-not-implemented")).toBeInTheDocument();
  });
});
