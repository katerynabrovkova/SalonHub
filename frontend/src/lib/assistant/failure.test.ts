import { describe, expect, it } from "vitest";

import { failureFor, type FailureCause } from "./failure";

// docs/DECISIONS.md § "Stage 17 sub-step order", 17.5b: the table is the
// single source for every way a turn can end other than `done`.

const BUSY = "Зараз у мене багато співрозмовниць. Спробуйте, будь ласка, за хвилинку 🤗";
const THROTTLED = "Ви пишете швидше, ніж я встигаю думати 😅 Зачекайте, будь ласка, хвилинку.";
const DAILY_LIMIT =
  "Я сьогодні наговорився досхочу і пішов на заслужений манікюр 💅 Повертайтесь завтра! А записатися можна й без мене:";
const BLOCKED = "На жаль, зараз я не можу відповісти. Записатися можна через форму на сайті.";
const NETWORK = "Схоже, зник зв'язок. Перевірте інтернет і спробуйте ще раз.";
const CUT_OFF = "Відповідь обірвалась. Спробуйте ще раз.";
const NOTHING_ARRIVED =
  "Ой, щось пішло не так, і я не встиг відповісти. Спробуйте, будь ласка, ще раз.";
const UNAVAILABLE = "Асистент зараз недоступний. Записатися можна через форму на сайті.";

const BOOK_YOURSELF = { label: "Записатись самостійно", href: "/services" };

const CASES: Array<{
  name: string;
  cause: FailureCause;
  text: string;
  retry: boolean;
  link: { label: string; href: string } | null;
}> = [
  {
    name: "busy",
    cause: { kind: "api", status: 503, code: "assistant_busy" },
    text: BUSY,
    retry: true,
    link: null,
  },
  {
    name: "throttled",
    cause: { kind: "api", status: 429, code: "throttled" },
    text: THROTTLED,
    retry: true,
    link: null,
  },
  {
    name: "daily_limit",
    cause: { kind: "api", status: 429, code: "assistant_daily_limit" },
    text: DAILY_LIMIT,
    retry: false,
    link: BOOK_YOURSELF,
  },
  {
    name: "identifier_blocked",
    cause: { kind: "event", code: "assistant_identifier_blocked", textReceived: false },
    text: BLOCKED,
    retry: false,
    link: BOOK_YOURSELF,
  },
  { name: "network", cause: { kind: "network" }, text: NETWORK, retry: true, link: null },
  {
    name: "turn_limit_after_text",
    cause: { kind: "event", code: "assistant_turn_limit", textReceived: true },
    text: CUT_OFF,
    retry: true,
    link: null,
  },
  {
    name: "provider_error_after_text",
    cause: { kind: "event", code: "assistant_provider_error", textReceived: true },
    text: CUT_OFF,
    retry: true,
    link: null,
  },
  {
    name: "internal_error_after_text",
    cause: { kind: "event", code: "internal_error", textReceived: true },
    text: CUT_OFF,
    retry: true,
    link: null,
  },
  {
    name: "no_done_after_text",
    cause: { kind: "no_done", textReceived: true },
    text: CUT_OFF,
    retry: true,
    link: null,
  },
  {
    name: "turn_limit_before_text",
    cause: { kind: "event", code: "assistant_turn_limit", textReceived: false },
    text: NOTHING_ARRIVED,
    retry: true,
    link: null,
  },
  {
    name: "provider_error_before_text",
    cause: { kind: "event", code: "assistant_provider_error", textReceived: false },
    text: NOTHING_ARRIVED,
    retry: true,
    link: null,
  },
  {
    name: "internal_error_before_text",
    cause: { kind: "event", code: "internal_error", textReceived: false },
    text: NOTHING_ARRIVED,
    retry: true,
    link: null,
  },
  {
    name: "no_done_before_text",
    cause: { kind: "no_done", textReceived: false },
    text: NOTHING_ARRIVED,
    retry: true,
    link: null,
  },
  {
    name: "not_found",
    cause: { kind: "api", status: 404, code: "not_found" },
    text: UNAVAILABLE,
    retry: false,
    link: BOOK_YOURSELF,
  },
  {
    name: "invalid",
    cause: { kind: "api", status: 400, code: "invalid" },
    text: NOTHING_ARRIVED,
    retry: false,
    link: null,
  },
  {
    name: "unknown_event_code",
    cause: { kind: "event", code: "something_new", textReceived: false },
    text: NOTHING_ARRIVED,
    retry: true,
    link: null,
  },
  {
    name: "unknown_api_code",
    cause: { kind: "api", status: 500, code: "internal_error" },
    text: NOTHING_ARRIVED,
    retry: true,
    link: null,
  },
];

describe("failureFor", () => {
  it.each(CASES)(
    "test_ending_maps_to_its_text_retry_and_link_($name)",
    ({ cause, text, retry, link }) => {
      expect(failureFor(cause)).toEqual({ text, retry, link });
    },
  );
});
