import { headers } from "next/headers";
import { notFound } from "next/navigation";
import Link from "next/link";

import { getSpecialistDetailPage } from "@/lib/specialists/getSpecialistDetailPage";
import { SALON_SLUG_HEADER } from "@/middleware";

import SpecialistCard from "../SpecialistCard";

interface SpecialistDetailPageProps {
  params: Promise<{ id: string }>;
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
      <SpecialistCard specialist={specialist} variant="detail" />

      <Link
        href={`/booking?entry=specialist&specialist=${id}&step=2`}
        className="self-center rounded bg-zinc-900 px-4 py-2 text-white dark:bg-zinc-100 dark:text-zinc-900"
      >
        Забронювати
      </Link>
    </main>
  );
}
