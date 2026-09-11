"use client";

import { useState } from "react";

import type { FlatReview } from "@/lib/reviews/getReviews";

// Matches the DRF pagination page size convention used elsewhere
// (getServicesPage.ts / getSpecialistsPage.ts's PAGE_SIZE) even though this
// endpoint has no server-side pagination — "показати ще" reveals 20 more of
// the already-fetched, already-sorted array client-side, no new fetch.
const PAGE_SIZE = 20;

function formatDate(createdAt: string): string {
  return new Intl.DateTimeFormat("uk-UA", { dateStyle: "long" }).format(new Date(createdAt));
}

function StarRating({ rating }: { rating: number }) {
  return (
    <span aria-label={`${rating}/5`} className="text-amber-500">
      {"★".repeat(rating)}
      <span className="text-zinc-300 dark:text-zinc-600">{"★".repeat(5 - rating)}</span>
    </span>
  );
}

export function ReviewsList({ reviews }: { reviews: FlatReview[] }) {
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const visibleReviews = reviews.slice(0, visibleCount);

  return (
    <>
      <div className="flex flex-col gap-4">
        {visibleReviews.map((review) => (
          <div
            key={review.id}
            className="flex flex-col gap-1 rounded border border-zinc-200 p-4 dark:border-zinc-700"
          >
            <StarRating rating={review.rating} />
            {review.text && (
              <p className="text-sm text-zinc-800 dark:text-zinc-200">{review.text}</p>
            )}
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {review.specialist.name} · {review.service.name}
            </p>
            <p className="text-xs text-zinc-500 dark:text-zinc-500">
              {formatDate(review.created_at)}
            </p>
          </div>
        ))}
      </div>

      {visibleCount < reviews.length && (
        <button
          type="button"
          onClick={() => setVisibleCount((count) => count + PAGE_SIZE)}
          className="self-center rounded border border-zinc-300 px-4 py-2 text-sm hover:border-zinc-500 dark:border-zinc-600 dark:hover:border-zinc-400"
        >
          Показати ще
        </button>
      )}
    </>
  );
}
