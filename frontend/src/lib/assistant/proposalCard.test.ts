import { describe, expect, it } from "vitest";

import { bookingSlotUrl, bookingTimeUrl, parseProposalCard, type ProposalCard } from "./proposalCard";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.6a; card shape from
// backend/ai_assistant/proposals.py (handle_propose_booking).

function card(overrides: Partial<ProposalCard> = {}): ProposalCard {
  return {
    service: { id: 7, name: "Манікюр з покриттям", duration_minutes: 60 },
    specialist: { id: 3, name: "Олена", average_rating: 4.8, review_count: 21 },
    price: "500.00",
    deposit: "100.00",
    due_at_visit: "400.00",
    currency: "UAH",
    slots: ["2026-11-07T10:00:00+02:00", "2026-11-07T12:30:00+02:00"],
    ...overrides,
  };
}

describe("parseProposalCard", () => {
  it("test_accepts_a_card_with_a_named_specialist", () => {
    const value = card();

    expect(parseProposalCard(JSON.parse(JSON.stringify(value)))).toEqual(value);
  });

  it("test_accepts_a_card_with_any_specialist", () => {
    const value = card({ specialist: "any", slots: [] });

    expect(parseProposalCard(JSON.parse(JSON.stringify(value)))).toEqual(value);
  });

  it.each([
    { name: "missing_service", value: { ...card(), service: undefined } },
    { name: "slots_not_an_array", value: { ...card(), slots: "2026-11-07T10:00:00+02:00" } },
    { name: "bad_specialist", value: { ...card(), specialist: { name: "Олена" } } },
  ])("test_returns_null_for_a_malformed_card_($name)", ({ value }) => {
    expect(parseProposalCard(value)).toBeNull();
  });
});

describe("booking links", () => {
  it("test_slot_url_goes_to_step_4_with_the_encoded_slot", () => {
    expect(bookingSlotUrl(card(), "2026-11-07T10:00:00+02:00")).toBe(
      "/booking?entry=service&service=7&specialist=3&step=4&slot=2026-11-07T10%3A00%3A00%2B02%3A00",
    );
  });

  it("test_slot_url_uses_specialist_any", () => {
    expect(bookingSlotUrl(card({ specialist: "any" }), "2026-11-07T10:00:00+02:00")).toBe(
      "/booking?entry=service&service=7&specialist=any&step=4&slot=2026-11-07T10%3A00%3A00%2B02%3A00",
    );
  });

  it("test_other_time_url_goes_to_step_3_from_the_first_slot_day", () => {
    const value = card({ slots: ["2026-11-08T11:00:00+02:00", "2026-11-09T09:00:00+02:00"] });

    expect(bookingTimeUrl(value)).toBe(
      "/booking?entry=service&service=7&specialist=3&step=3&date_from=2026-11-08",
    );
  });

  it("test_choose_time_url_has_no_date_from", () => {
    expect(bookingTimeUrl(card({ specialist: "any", slots: [] }))).toBe(
      "/booking?entry=service&service=7&specialist=any&step=3",
    );
  });
});
