import { headers } from "next/headers";
import Link from "next/link";

import { getServicesPage } from "@/lib/catalog/getServicesPage";
import { SALON_SLUG_HEADER } from "@/middleware";

interface ServicesPageProps {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}

function parsePage(value: string | string[] | undefined): number {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = raw === undefined ? 1 : Number.parseInt(raw, 10);
  return Number.isNaN(parsed) || parsed < 1 ? 1 : parsed;
}

export default async function ServicesPage({ searchParams }: ServicesPageProps) {
  const slug = (await headers()).get(SALON_SLUG_HEADER);
  const page = parsePage((await searchParams).page);

  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>The platform is still in development.</p>
      </main>
    );
  }

  const { services, currentPage, totalPages } = await getServicesPage(slug, page);

  if (totalPages === 0) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>Послуги ще не додано.</p>
      </main>
    );
  }

  return (
    <main className="flex flex-col gap-6 p-8">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {services.map((service) => (
          <Link
            key={service.id}
            href={`/services/${service.id}`}
            className="block rounded border border-zinc-200 p-4 hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
          >
            <h2 className="font-semibold">{service.name}</h2>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {service.duration_minutes} хв
            </p>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">{service.price}</p>
          </Link>
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
