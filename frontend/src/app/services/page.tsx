import { headers } from "next/headers";
import Link from "next/link";

import { getServiceCategories, type ServiceCategory } from "@/lib/catalog/getServiceCategories";
import { getServicesPage } from "@/lib/catalog/getServicesPage";
import { initials } from "@/lib/format/initials";
import { SALON_SLUG_HEADER } from "@/middleware";

interface ServicesPageProps {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}

function parsePage(value: string | string[] | undefined): number {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = raw === undefined ? 1 : Number.parseInt(raw, 10);
  return Number.isNaN(parsed) || parsed < 1 ? 1 : parsed;
}

function parseCategory(value: string | string[] | undefined): string | undefined {
  const raw = Array.isArray(value) ? value[0] : value;
  return raw === undefined ? undefined : raw;
}

function CategoryGrid({ categories }: { categories: ServiceCategory[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {categories.map((category) => (
        <Link
          key={category.id}
          href={`/services?category=${category.id}`}
          className="flex flex-col gap-2 rounded border border-zinc-200 p-4 hover:border-zinc-400 dark:border-zinc-700 dark:hover:border-zinc-500"
        >
          {category.photo ? (
            // eslint-disable-next-line @next/next/no-img-element -- plain <img>, matches specialists/page.tsx's established convention (no next/image config for salon-hosted photos yet)
            <img
              src={category.photo}
              alt={category.name}
              className="h-32 w-32 self-center rounded-full object-cover"
            />
          ) : (
            <div
              className="flex h-32 w-32 items-center justify-center self-center rounded-full bg-zinc-200 text-lg font-semibold text-zinc-600 dark:bg-zinc-700 dark:text-zinc-300"
              aria-hidden="true"
            >
              {initials(category.name) || "?"}
            </div>
          )}

          <h2 className="text-center font-semibold">{category.name}</h2>
        </Link>
      ))}
    </div>
  );
}

export default async function ServicesPage({ searchParams }: ServicesPageProps) {
  const slug = (await headers()).get(SALON_SLUG_HEADER);

  if (slug === null) {
    return (
      <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
        <p>The platform is still in development.</p>
      </main>
    );
  }

  const resolvedSearchParams = await searchParams;
  const category = parseCategory(resolvedSearchParams.category);

  if (category === undefined) {
    const categories = await getServiceCategories(slug);

    if (categories.length === 0) {
      return (
        <main className="p-8 text-center text-zinc-600 dark:text-zinc-400">
          <p>Категорії ще не додано.</p>
        </main>
      );
    }

    return (
      <main className="flex flex-col gap-6 p-8">
        <CategoryGrid categories={categories} />
      </main>
    );
  }

  const page = parsePage(resolvedSearchParams.page);
  const { services, currentPage, totalPages } = await getServicesPage(slug, page, category);

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
          <Link href={`?page=${currentPage - 1}&category=${category}`}>← Назад</Link>
        ) : (
          <span className="text-zinc-400">← Назад</span>
        )}
        <span>
          Сторінка {currentPage} з {totalPages}
        </span>
        {currentPage < totalPages ? (
          <Link href={`?page=${currentPage + 1}&category=${category}`}>Далі →</Link>
        ) : (
          <span className="text-zinc-400">Далі →</span>
        )}
      </nav>
    </main>
  );
}
