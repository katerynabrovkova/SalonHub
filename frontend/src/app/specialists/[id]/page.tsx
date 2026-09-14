import { headers } from "next/headers";
import { notFound } from "next/navigation";
import Link from "next/link";

import { initials } from "@/lib/format/initials";
import {
  getSpecialistDetailPage,
  type SpecialistDetail,
} from "@/lib/specialists/getSpecialistDetailPage";
import { SALON_SLUG_HEADER } from "@/middleware";

interface SpecialistDetailPageProps {
  params: Promise<{ id: string }>;
}

function RatingLine({ specialist }: { specialist: SpecialistDetail }) {
  if (specialist.review_count === 0) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">Немає відгуків</p>;
  }
  return (
    <p className="text-sm text-zinc-600 dark:text-zinc-400">
      ★ {specialist.average_rating?.toFixed(1)} ({specialist.review_count} відгуків)
    </p>
  );
}

export default async function SpecialistDetailPage({ params }: SpecialistDetailPageProps) {
  const slug = (await headers()).get(SALON_SLUG_HEADER);

  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>The platform is still in development.</p>
      </main>
    );
  }

  const id = Number((await params).id);
  if (Number.isNaN(id)) {
    notFound();
  }

  const specialist = await getSpecialistDetailPage(slug, id);
  if (specialist === null) {
    notFound();
  }

  return (
    <main className="flex flex-col gap-4 p-8">
      {specialist.photo ? (
        // eslint-disable-next-line @next/next/no-img-element -- plain <img>, matches specialists/page.tsx's established convention (no next/image config for salon-hosted photos yet)
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

      <h1 className="text-center text-xl font-semibold">{specialist.name}</h1>
      {specialist.bio && (
        <p className="text-center text-sm text-zinc-600 dark:text-zinc-400">{specialist.bio}</p>
      )}

      <div className="text-center">
        <RatingLine specialist={specialist} />
      </div>

      <Link
        href={`/booking?entry=specialist&specialist=${id}&step=2`}
        className="self-center rounded bg-zinc-900 px-4 py-2 text-white dark:bg-zinc-100 dark:text-zinc-900"
      >
        Забронювати
      </Link>
    </main>
  );
}
