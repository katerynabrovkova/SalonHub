"use client";

/**
 * Client profile page (docs/DECISIONS.md § Stage 15 planning, item 7).
 * Reached from the dashboard's avatar dropdown ("Профіль",
 * app/client/ClientAvatarMenu.tsx). Auth is already enforced by
 * app/client/layout.tsx (this route is inside its subtree) -- this page
 * does no auth checking of its own.
 *
 * Each row is a fully clickable link to its own not-yet-built sub-page
 * (/client/profile/email, /name, /phone, /password -- items 8-11, separate
 * follow-ups). Those routes don't exist yet, so clicking a row 404s
 * harmlessly today; no fake stub pages are added here for them.
 *
 * Ім'я/Телефон rows are rendered ONLY when `me.name`/`me.phone` are
 * non-null (backend/accounts/serializers.py's MeSerializer: null means "no
 * linked Customer yet", a real, not-rare state per item 5's recon) -- never
 * shown as an empty or placeholder row.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";

import { useAuth } from "@/app/AuthContext";

interface ProfileRowProps {
  label: string;
  value: string;
  href: string;
}

function ProfileRow({ label, value, href }: ProfileRowProps) {
  return (
    <Link
      href={href}
      className="flex items-center justify-between gap-4 rounded px-2 py-3 hover:bg-zinc-50 dark:hover:bg-zinc-800"
    >
      <span className="flex flex-col text-left">
        <span className="text-sm text-zinc-500 dark:text-zinc-400">{label}</span>
        <span>{value}</span>
      </span>
      <span aria-hidden="true" className="text-zinc-400">
        &rsaquo;
      </span>
    </Link>
  );
}

export default function ClientProfilePage() {
  const router = useRouter();
  const { me, logout } = useAuth();

  async function handleLogout() {
    // Native confirm(), not a custom modal -- same reasoning as
    // ClientAvatarMenu's own logout action (no design system yet, deferred
    // to Stages 18-21). Identical confirmation text in both locations.
    if (!window.confirm("Ви впевнені, що хочете вийти?")) {
      return;
    }
    await logout();
    // Same "don't rely solely on the layout's redirect" reasoning as
    // ClientAvatarMenu's own logout action -- navigate explicitly.
    router.replace("/login");
  }

  // Defensive only: app/client/layout.tsx never renders this subtree unless
  // `me` is already non-null, so this never actually returns null in
  // practice.
  if (me === null) {
    return null;
  }

  return (
    <main className="flex flex-col gap-8 p-8">
      <Link href="/client" className="text-sm text-blue-600 underline">
        &larr; Мої записи
      </Link>

      <h1 className="text-xl font-semibold">Профіль</h1>

      <section className="flex flex-col gap-1">
        <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
          Основна інформація
        </h2>
        <ProfileRow label="Email" value={me.email} href="/client/profile/email" />
        {me.name !== null ? (
          <ProfileRow label="Ім'я" value={me.name} href="/client/profile/name" />
        ) : null}
        {me.phone !== null ? (
          <ProfileRow label="Телефон" value={me.phone} href="/client/profile/phone" />
        ) : null}
      </section>

      <section className="flex flex-col gap-1">
        <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Безпека</h2>
        <ProfileRow label="Пароль" value="••••••••" href="/client/profile/password" />
      </section>

      <button
        type="button"
        onClick={() => void handleLogout()}
        className="self-start text-sm text-red-600 underline"
      >
        Вийти
      </button>
    </main>
  );
}
