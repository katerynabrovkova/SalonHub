"use client";

/**
 * The assistant's proposal card in the chat (docs/DECISIONS.md § "Stage 17
 * sub-step order", 17.6a). Display only: every value comes from the
 * backend, and every target is the ordinary booking form, which re-checks
 * the slot. Neutral styling; the look is finished in the design pass.
 *
 * Slots: 2-3 on one day show a date line and time chips; on different days
 * each chip shows its day; 1 slot is one "Записатися на …" button; with
 * slots there is also "Інший день або час", without slots only "Обрати
 * час". `onNavigate` runs on every link click (the widget minimises the
 * phone chat).
 */

import Link from "next/link";

import { bookingSlotUrl, bookingTimeUrl, type ProposalCard } from "@/lib/assistant/proposalCard";
import { formatSlotForSummary } from "@/lib/booking/formatSlotForSummary";
import { reviewCount } from "@/lib/i18n/reviewCount";
import { formatPrice } from "@/lib/pricing/formatPrice";

interface AssistantProposalCardProps {
  card: ProposalCard;
  onNavigate?: () => void;
}

const CHIP =
  "inline-flex min-h-11 items-center rounded-full border border-zinc-300 px-4 text-sm hover:border-zinc-500 dark:border-zinc-600";
const PRIMARY =
  "inline-flex min-h-11 items-center rounded-full bg-zinc-900 px-4 text-sm text-white dark:bg-zinc-100 dark:text-zinc-900";
const SECONDARY = "inline-flex min-h-11 items-center text-sm underline";

// Calendar parts come from the ISO string itself, never from the browser's
// time zone (same reasoning as formatSlotForSummary).
function slotDate(iso: string): Date {
  const [year, month, day] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

/** "Сб": Intl gives "сб"; the card capitalises it. */
function weekday(iso: string): string {
  const short = new Intl.DateTimeFormat("uk-UA", { weekday: "short", timeZone: "UTC" }).format(
    slotDate(iso),
  );
  return short.charAt(0).toUpperCase() + short.slice(1);
}

/** "Сб, 7 листопада". */
function dateLine(iso: string): string {
  const dayMonth = new Intl.DateTimeFormat("uk-UA", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
  }).format(slotDate(iso));
  return `${weekday(iso)}, ${dayMonth}`;
}

function time(iso: string): string {
  return iso.slice(11, 16);
}

function specialistLine(card: ProposalCard): string {
  const minutes = `${card.service.duration_minutes} хв`;
  const { specialist } = card;
  if (specialist === "any") {
    return `Будь-який майстер · ${minutes}`;
  }
  if (specialist.review_count === 0) {
    return `${specialist.name} · Немає відгуків · ${minutes}`;
  }
  const rating = specialist.average_rating?.toFixed(1) ?? "–";
  return `${specialist.name} · ★ ${rating} (${reviewCount(specialist.review_count)}) · ${minutes}`;
}

function Slots({ card, onNavigate }: AssistantProposalCardProps) {
  const { slots } = card;

  if (slots.length === 0) {
    return (
      <Link href={bookingTimeUrl(card)} onClick={onNavigate} className={PRIMARY}>
        Обрати час
      </Link>
    );
  }

  const otherTime = (
    <Link href={bookingTimeUrl(card)} onClick={onNavigate} className={SECONDARY}>
      Інший день або час
    </Link>
  );

  if (slots.length === 1) {
    return (
      <div className="flex flex-col items-start gap-1">
        <Link href={bookingSlotUrl(card, slots[0])} onClick={onNavigate} className={PRIMARY}>
          {`Записатися на ${formatSlotForSummary(slots[0])}`}
        </Link>
        {otherTime}
      </div>
    );
  }

  const sameDay = slots.every((slot) => slot.slice(0, 10) === slots[0].slice(0, 10));
  return (
    <div className="flex flex-col items-start gap-2">
      {sameDay ? <p className="text-sm">{dateLine(slots[0])}</p> : null}
      <div className="flex flex-wrap gap-2">
        {slots.map((slot) => (
          <Link key={slot} href={bookingSlotUrl(card, slot)} onClick={onNavigate} className={CHIP}>
            {sameDay ? time(slot) : `${weekday(slot)} ${time(slot)}`}
          </Link>
        ))}
      </div>
      {otherTime}
    </div>
  );
}

export default function AssistantProposalCard({ card, onNavigate }: AssistantProposalCardProps) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-zinc-200 bg-white p-3 text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100">
      <p className="font-semibold">{card.service.name}</p>
      <p className="text-sm text-zinc-600 dark:text-zinc-400">{specialistLine(card)}</p>
      <div className="text-sm">
        <p>Вартість {formatPrice(card.price, card.currency)}</p>
        <p>
          <strong>Передоплата онлайн {formatPrice(card.deposit, card.currency)}</strong>
        </p>
        <p>На місці {formatPrice(card.due_at_visit, card.currency)}</p>
      </div>
      <Slots card={card} onNavigate={onNavigate} />
    </div>
  );
}
