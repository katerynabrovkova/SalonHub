// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import BookingSummary from "./BookingSummary";

// Intl-formatted text can contain U+00A0 (non-breaking space) instead of a
// regular space (CLAUDE.md § "`Intl.NumberFormat` inserts U+00A0 ..." --
// applies equally to Intl.DateTimeFormat output here). A literal string
// passed to getByText won't match that reliably, so whitespace runs are
// matched via \s+ instead of a literal-string comparison.
function textMatcher(expected: string): RegExp {
  const escaped = expected.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(escaped.replace(/\s+/g, "\\s+"));
}

describe("BookingSummary", () => {
  it("test_renders_service_specialist_and_formatted_slot", () => {
    render(
      <BookingSummary
        serviceName="Manicure"
        specialistName="Olena"
        slot="2026-09-24T14:30:00+03:00"
      />,
    );

    expect(screen.getByText("Manicure")).toBeInTheDocument();
    expect(screen.getByText("Olena")).toBeInTheDocument();
    expect(screen.getByText(textMatcher("24 вересня, 14:30"))).toBeInTheDocument();
  });

  it("test_null_specialist_shows_any_specialist_fallback_text", () => {
    render(
      <BookingSummary serviceName="Manicure" specialistName={null} slot="2026-09-24T14:30:00+03:00" />,
    );

    expect(screen.getByText("Будь-який спеціаліст")).toBeInTheDocument();
  });

  it("test_slot_text_comes_from_the_iso_strings_own_wall_time_not_utc", () => {
    // Same +09:00 case as formatSlotForSummary.test.ts's own proof, exercised
    // through the rendered component this time.
    render(
      <BookingSummary
        serviceName="Manicure"
        specialistName="Olena"
        slot="2026-09-24T01:30:00+09:00"
      />,
    );

    expect(screen.getByText(textMatcher("24 вересня, 01:30"))).toBeInTheDocument();
  });
});
