import { headers } from "next/headers";
import Link from "next/link";

import { getSpecialistsPage } from "@/lib/specialists/getSpecialistsPage";
import { SALON_SLUG_HEADER } from "@/middleware";

import SpecialistCard from "./SpecialistCard";

interface SpecialistsPageProps {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}

function parsePage(value: string | string[] | undefined): number {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = raw === undefined ? 1 : Number.parseInt(raw, 10);
  return Number.isNaN(parsed) || parsed < 1 ? 1 : parsed;
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
          <SpecialistCard key={specialist.id} specialist={specialist} variant="grid" />
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
