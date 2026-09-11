import { headers } from "next/headers";
import { notFound } from "next/navigation";

import { getServiceDetailPage } from "@/lib/catalog/getServiceDetailPage";
import { SALON_SLUG_HEADER } from "@/middleware";

interface ServiceDetailPageProps {
  params: Promise<{ id: string }>;
}

export default async function ServiceDetailPage({ params }: ServiceDetailPageProps) {
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

  const data = await getServiceDetailPage(slug, id);
  if (data === null) {
    notFound();
  }

  const { service, specialists } = data;

  return (
    <main className="flex flex-col gap-6 p-8">
      <div>
        <h1 className="text-xl font-semibold">{service.name}</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {service.duration_minutes} хв
        </p>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">{service.price}</p>
      </div>

      <div>
        <h2 className="font-semibold">Хто надає цю послугу</h2>
        {specialists.length === 0 ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Спеціалістів поки не призначено.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {specialists.map((specialist) => (
              <li key={specialist.id}>
                <p className="font-medium">{specialist.name}</p>
                <p className="text-sm text-zinc-600 dark:text-zinc-400">{specialist.bio}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}
