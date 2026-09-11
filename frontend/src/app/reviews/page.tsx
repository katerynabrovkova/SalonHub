import { headers } from "next/headers";

import { getReviews } from "@/lib/reviews/getReviews";
import { SALON_SLUG_HEADER } from "@/middleware";

import { ReviewsList } from "./ReviewsList";

export default async function ReviewsPage() {
  const slug = (await headers()).get(SALON_SLUG_HEADER);

  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>The platform is still in development.</p>
      </main>
    );
  }

  const reviews = await getReviews(slug);

  if (reviews.length === 0) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>Відгуків ще не додано.</p>
      </main>
    );
  }

  return (
    <main className="flex flex-col gap-6 p-8">
      <ReviewsList reviews={reviews} />
    </main>
  );
}
