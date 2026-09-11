import { headers } from "next/headers";
import Link from "next/link";

import { getSpecialistsPage, type Specialist } from "@/lib/specialists/getSpecialistsPage";
import { SALON_SLUG_HEADER } from "@/middleware";

interface SpecialistsPageProps {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}

function parsePage(value: string | string[] | undefined): number {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = raw === undefined ? 1 : Number.parseInt(raw, 10);
  return Number.isNaN(parsed) || parsed < 1 ? 1 : parsed;
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function RatingLine({ specialist }: { specialist: Specialist }) {
  if (specialist.review_count === 0) {
    return <p className="text-sm text-zinc-500 dark:text-zinc-400">Немає відгуків</p>;
  }
  return (
    <p className="text-sm text-zinc-600 dark:text-zinc-400">
      ★ {specialist.average_rating?.toFixed(1)} ({specialist.review_count} відгуків)
    </p>
  );
}

export default async function SpecialistsPage({ searchParams }: SpecialistsPageProps) {
  const slug = (await headers()).get(SALON_SLUG_HEADER);
  const page = parsePage((await searchParams).page);

  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>The platform is still in development.</p>
      </main>
    );
  }

  const { specialists, currentPage, totalPages } = await getSpecialistsPage(slug, page);

  if (totalPages === 0) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>Спеціалістів ще не додано.</p>
      </main>
    );
  }

  return (
    <main className="flex flex-col gap-6 p-8">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {specialists.map((specialist) => (
          <div
            key={specialist.id}
            className="flex flex-col gap-2 rounded border border-zinc-200 p-4 dark:border-zinc-700"
          >
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

            <h2 className="text-center font-semibold">{specialist.name}</h2>
            {specialist.bio && (
              <p className="text-sm text-zinc-600 dark:text-zinc-400">{specialist.bio}</p>
            )}

            <RatingLine specialist={specialist} />

            {specialist.services.length > 0 && (
              <p className="text-sm text-zinc-600 dark:text-zinc-400">
                {specialist.services.map((service) => service.name).join(", ")}
              </p>
            )}
          </div>
        ))}
      </div>

      <nav className="flex items-center justify-center gap-4">
        {currentPage > 1 ? (
          <Link href={`?page=${currentPage - 1}`}>← Назад</Link>
        ) : (
          <span className="text-zinc-400">← Назад</span>
        )}
        <span>
          Сторінка {currentPage} з {totalPages}
        </span>
        {currentPage < totalPages ? (
          <Link href={`?page=${currentPage + 1}`}>Далі →</Link>
        ) : (
          <span className="text-zinc-400">Далі →</span>
        )}
      </nav>
    </main>
  );
}
