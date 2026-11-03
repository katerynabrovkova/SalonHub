/**
 * What the widget shows when a turn ends other than with `done`
 * (docs/DECISIONS.md § "Stage 17 sub-step order", 17.5b and its table, the
 * single source for these texts, retry and links).
 *
 * The widget describes how the turn ended; this module decides the text,
 * whether "Спробувати ще раз" is offered and whether the "book it yourself"
 * link to the services page is shown. An unknown code gets the "nothing
 * arrived" row, with retry.
 */

export type FailureCause =
  /** A refusal before the stream: ApiError status and envelope code. */
  | { kind: "api"; status: number; code: string }
  /** An `error` event in the stream. */
  | { kind: "event"; code: string; textReceived: boolean }
  /** fetch or the stream read failed: the connection was lost. */
  | { kind: "network" }
  /** The stream ended without `done` or `error`. */
  | { kind: "no_done"; textReceived: boolean };

export interface FailureLink {
  label: string;
  href: string;
}

export interface Failure {
  text: string;
  retry: boolean;
  link: FailureLink | null;
}

const BOOK_YOURSELF: FailureLink = { label: "Записатись самостійно", href: "/services" };

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

function withRetry(text: string): Failure {
  return { text, retry: true, link: null };
}

function withLink(text: string): Failure {
  return { text, retry: false, link: BOOK_YOURSELF };
}

/** A reply that failed mid-turn: cut off if text had begun, else nothing arrived. */
function stopped(textReceived: boolean): Failure {
  return withRetry(textReceived ? CUT_OFF : NOTHING_ARRIVED);
}

export function failureFor(cause: FailureCause): Failure {
  switch (cause.kind) {
    case "network":
      return withRetry(NETWORK);
    case "no_done":
      return stopped(cause.textReceived);
    case "event":
      switch (cause.code) {
        case "assistant_identifier_blocked":
          return withLink(BLOCKED);
        case "assistant_turn_limit":
        case "assistant_provider_error":
        case "internal_error":
          return stopped(cause.textReceived);
        default:
          return withRetry(NOTHING_ARRIVED);
      }
    case "api":
      switch (cause.code) {
        case "assistant_busy":
          return withRetry(BUSY);
        case "throttled":
          return withRetry(THROTTLED);
        case "assistant_daily_limit":
          return withLink(DAILY_LIMIT);
        case "not_found":
          return withLink(UNAVAILABLE);
        case "invalid":
          // The widget never sends an invalid message; no retry, it would
          // fail the same way.
          return { text: NOTHING_ARRIVED, retry: false, link: null };
        default:
          return withRetry(NOTHING_ARRIVED);
      }
  }
}
