/**
 * Shared presentational card for a specialist (photo/initials fallback +
 * name + bio + rating line), extracted from the inline JSX previously
 * duplicated in specialists/page.tsx and specialists/[id]/page.tsx
 * (docs/DECISIONS.md § "Stage 14 UI decisions: booking step 2 for
 * entry=service (specialist selection)"). Booking step 2 for `entry=service`
 * is the third caller of this visual pattern, which is why extraction
 * happens now rather than at the Stage 13 point that first deferred it.
 *
 * `variant` picks between the two existing visual contexts without changing
 * either one: "grid" keeps the bordered/padded card box used on /specialists
 * and appends the comma-joined services list; "detail" keeps the unboxed,
 * centered layout used on /specialists/[id] (spacing matches the original
 * because flexbox `gap` is measured between box edges regardless of
 * nesting, so wrapping these same elements in an inner `flex flex-col
 * gap-4` div reproduces the parent `<main>`'s original gap-4 spacing
 * exactly).
 *
 * "list" is the /specialists catalog card (docs/DECISIONS.md § "Fix:
 * specialist list cards lead to the specialist page"): the "grid" card
 * rendered as one whole-card link to /specialists/<id>, named by the
 * specialist's name only, with a decorative chevron and nothing interactive
 * inside. It is a separate variant because booking step 2
 * (SpecialistSelectionGrid) puts the "grid" card inside a radio label, where
 * a link would be a nested interactive element.
 */
import Link from "next/link";

import { initials } from "@/lib/format/initials";

export interface SpecialistCardData {
  /** Required for the "list" variant only (enforced by SpecialistCardProps). */
  id?: number;
  name: string;
  bio: string;
  photo: string | null;
  average_rating: number | null;
  review_count: number;
  services?: { id: number; name: string }[];
}

type SpecialistCardProps =
  | { specialist: SpecialistCardData; variant: "grid" | "detail" }
  | { specialist: SpecialistCardData & { id: number }; variant: "list" };

function RatingLine({ specialist }: { specialist: SpecialistCardData }) {
  if (specialist.review_count === 0) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">Немає відгуків</p>;
  }
  return (
    <p className="text-sm text-zinc-600 dark:text-zinc-400">
      ★ {specialist.average_rating?.toFixed(1)} ({specialist.review_count} відгуків)
    </p>
  );
}

export default function SpecialistCard(props: SpecialistCardProps) {
  const { specialist, variant } = props;
  const isDetail = variant === "detail";
  const nameId = props.variant === "list" ? `specialist-${props.specialist.id}-name` : undefined;

  const content = (
    <>
      {specialist.photo ? (
        // eslint-disable-next-line @next/next/no-img-element -- plain <img>, matches this route's established convention (no next/image config for salon-hosted photos yet)
        <img
          src={specialist.photo}
          alt={specialist.name}
          className="h-32 w-32 self-center rounded-full object-cover"
        />
      ) : (
        <div
          className="flex h-32 w-32 items-center justify-center self-center rounded-full bg-zinc-200 text-lg font-semibold text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300"
          aria-hidden="true"
        >
          {initials(specialist.name) || "?"}
        </div>
      )}

      {isDetail ? (
        <h1 className="text-center text-xl font-semibold">{specialist.name}</h1>
      ) : (
        <h2 id={nameId} className="text-center font-semibold">
          {specialist.name}
        </h2>
      )}

      {specialist.bio && (
        <p
          className={
            isDetail
              ? "text-center text-sm text-zinc-600 dark:text-zinc-400"
              : "text-sm text-zinc-600 dark:text-zinc-400"
          }
        >
          {specialist.bio}
        </p>
      )}

      {isDetail ? (
        <div className="text-center">
          <RatingLine specialist={specialist} />
        </div>
      ) : (
        <RatingLine specialist={specialist} />
      )}

      {!isDetail && specialist.services && specialist.services.length > 0 && (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {specialist.services.map((service) => service.name).join(", ")}
        </p>
      )}
    </>
  );

  if (props.variant === "list") {
    return (
      <Link
        href={`/specialists/${props.specialist.id}`}
        aria-labelledby={nameId}
        className="relative flex flex-col gap-2 rounded border border-zinc-200 p-4 hover:border-zinc-400 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 dark:border-zinc-700 dark:hover:border-zinc-500"
      >
        {content}
        <svg
          aria-hidden="true"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="absolute top-4 right-4 h-5 w-5 text-zinc-400"
        >
          <path d="M7.5 4.5 13 10l-5.5 5.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </Link>
    );
  }

  return (
    <div
      className={
        isDetail
          ? "flex flex-col gap-4"
          : "flex flex-col gap-2 rounded border border-zinc-200 p-4 dark:border-zinc-700"
      }
    >
      {content}
    </div>
  );
}
