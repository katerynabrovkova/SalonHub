"use client";

/**
 * Avatar + dropdown menu on the client dashboard's header (docs/DECISIONS.md
 * § Stage 15 planning, item 7). Extracted into its own file, not inlined in
 * page.tsx, for the same reason ServiceInfoPopover.tsx/ContactInfoForm.tsx/
 * DateTimeSelectionGrid.tsx are their own files despite each having exactly
 * one call site: it isolates the dropdown's open/close state and
 * click-outside handling from the dashboard's own data-fetching state, and
 * makes both independently testable.
 *
 * No existing popover/dropdown precedent to reuse: ServiceInfoPopover.tsx is
 * the only prior "popover"-shaped component in this codebase, but it's built
 * on the native <dialog> element (showModal(), a full-screen backdrop, focus
 * trap, Escape-to-close all for free) -- a different UX than a small corner
 * menu anchored under a trigger button. This is deliberately NOT a <dialog>:
 * an anchored dropdown has no backdrop to click, so "click outside closes
 * it" needs its own `mousedown` listener on `document`, per the task's own
 * "plain conditional rendering + click-outside-to-close" instruction. No
 * external library.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useAuth } from "@/app/AuthContext";

function initialOf(label: string): string {
  return label.charAt(0).toUpperCase();
}

export default function ClientAvatarMenu() {
  const router = useRouter();
  const { me, logout } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    function handlePointerDown(event: MouseEvent) {
      if (
        containerRef.current !== null &&
        event.target instanceof Node &&
        !containerRef.current.contains(event.target)
      ) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [isOpen]);

  async function handleLogout() {
    // Native confirm(), not a custom modal -- no design system yet
    // (deferred to Stages 18-21), not worth building styled UI for
    // something this replaceable later.
    if (!window.confirm("Ви впевнені, що хочете вийти?")) {
      return;
    }
    setIsOpen(false);
    await logout();
    // client/layout.tsx would eventually redirect here too once `me` settles
    // to null on next render, but that's not relied on for a clean
    // transition -- navigate explicitly, right after the session is gone.
    router.replace("/login");
  }

  // Defensive only: app/client/layout.tsx never renders this subtree unless
  // `me` is already non-null, so this never actually returns null in
  // practice.
  if (me === null) {
    return null;
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-haspopup="true"
        aria-expanded={isOpen}
        aria-label="Меню акаунту"
        className="flex h-9 w-9 items-center justify-center rounded-full bg-zinc-200 text-sm font-medium text-zinc-700 dark:bg-zinc-700 dark:text-zinc-200"
      >
        {initialOf(me.name ?? me.email)}
      </button>

      {isOpen ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-10 mt-2 flex w-40 flex-col gap-1 rounded border border-zinc-200 bg-white p-2 shadow dark:border-zinc-700 dark:bg-zinc-900"
        >
          <Link
            href="/client/profile"
            role="menuitem"
            onClick={() => setIsOpen(false)}
            className="rounded px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            Профіль
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={() => void handleLogout()}
            className="rounded px-2 py-1 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            Вийти
          </button>
        </div>
      ) : null}
    </div>
  );
}
