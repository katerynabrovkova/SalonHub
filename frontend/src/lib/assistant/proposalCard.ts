/**
 * The assistant's proposal card (docs/DECISIONS.md § "Stage 17 sub-step
 * order", 17.6a; Stage 16 "Proposal card" and "Sub-step 6 details", 6b).
 * The backend computes every value (backend/ai_assistant/proposals.py); the
 * frontend only checks the shape and builds the booking links.
 *
 * Cards from history are data stored earlier, so their shape is checked:
 * a malformed card is dropped, never rendered half-way.
 */

export interface ProposalSpecialist {
  id: number;
  name: string;
  average_rating: number | null;
  review_count: number;
}

export interface ProposalCard {
  service: { id: number; name: string; duration_minutes: number };
  specialist: "any" | ProposalSpecialist;
  price: string;
  deposit: string;
  due_at_visit: string;
  currency: string;
  /** 0-3 salon-local ISO start times with offset, sorted. */
  slots: string[];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseService(value: unknown): ProposalCard["service"] | null {
  if (!isObject(value)) {
    return null;
  }
  const { id, name, duration_minutes } = value;
  if (!isNumber(id) || typeof name !== "string" || !isNumber(duration_minutes)) {
    return null;
  }
  return { id, name, duration_minutes };
}

function parseSpecialist(value: unknown): ProposalCard["specialist"] | null {
  if (value === "any") {
    return "any";
  }
  if (!isObject(value)) {
    return null;
  }
  const { id, name, average_rating, review_count } = value;
  if (
    !isNumber(id) ||
    typeof name !== "string" ||
    !(average_rating === null || isNumber(average_rating)) ||
    !isNumber(review_count)
  ) {
    return null;
  }
  return { id, name, average_rating, review_count };
}

/** The card, or null when any field is missing or has the wrong type. */
export function parseProposalCard(value: unknown): ProposalCard | null {
  if (!isObject(value)) {
    return null;
  }
  const service = parseService(value.service);
  const specialist = parseSpecialist(value.specialist);
  const { price, deposit, due_at_visit, currency, slots } = value;
  if (
    service === null ||
    specialist === null ||
    typeof price !== "string" ||
    typeof deposit !== "string" ||
    typeof due_at_visit !== "string" ||
    typeof currency !== "string" ||
    !Array.isArray(slots) ||
    !slots.every((slot) => typeof slot === "string")
  ) {
    return null;
  }
  return { service, specialist, price, deposit, due_at_visit, currency, slots };
}

function bookingBase(card: ProposalCard): string {
  const specialist = card.specialist === "any" ? "any" : String(card.specialist.id);
  // Booking links use entry=service (Stage 16, "Sub-step 6 details").
  return `/booking?entry=service&service=${card.service.id}&specialist=${specialist}`;
}

/** Step 4 (contact) for one shown time. The slot's "+" offset must be encoded. */
export function bookingSlotUrl(card: ProposalCard, slot: string): string {
  return `${bookingBase(card)}&step=4&slot=${encodeURIComponent(slot)}`;
}

/**
 * Step 3 (choose a time). With shown slots this is "Інший день або час" and
 * starts from the first shown slot's day; without slots it is "Обрати час"
 * and starts from today (no date_from).
 */
export function bookingTimeUrl(card: ProposalCard): string {
  const base = `${bookingBase(card)}&step=3`;
  return card.slots.length === 0 ? base : `${base}&date_from=${card.slots[0].slice(0, 10)}`;
}
