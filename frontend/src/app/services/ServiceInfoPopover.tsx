"use client";

/**
 * Client Component boundary for the service-info popup (docs/DECISIONS.md
 * § Stage 13 reopened: implementation decisions for the popup, category
 * fetch, and detail-page removal). services/page.tsx stays a Server
 * Component; this is the only "use client" in that tree.
 *
 * Renders both the "i" trigger button and the <dialog> itself: the trigger
 * has to live here too, not in the parent Server Component, since a Server
 * Component can't attach an onClick handler or hold the open/close state.
 * State is kept internally (a plain useState) rather than exposed via an
 * imperative handle — nothing outside this component ever needs to open or
 * close it, so there's no reason to hand control to the parent.
 */
import { useRef, useState } from "react";

interface ServiceInfoPopoverProps {
  service: {
    name: string;
    description: string | null;
    duration_minutes: number;
    price: string;
    category?: { name: string };
  };
}

export default function ServiceInfoPopover({ service }: ServiceInfoPopoverProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [isOpen, setIsOpen] = useState(false);

  function handleOpen() {
    setIsOpen(true);
    dialogRef.current?.showModal();
  }

  function handleClose() {
    dialogRef.current?.close();
    setIsOpen(false);
  }

  return (
    <>
      <button
        type="button"
        aria-label="Інформація про послугу"
        onClick={handleOpen}
        className="relative z-10 rounded-full border border-zinc-300 px-2 text-xs leading-5 text-zinc-600 hover:border-zinc-400 dark:border-zinc-600 dark:text-zinc-300"
      >
        i
      </button>

      {/* onClose covers the native close paths (Escape key, <form
          method="dialog">) that don't go through handleClose, keeping
          isOpen in sync either way. onClick closes on a ::backdrop click:
          showModal() gives focus-trap and Escape-to-close for free, but not
          click-outside — a backdrop click's event.target is the <dialog>
          element itself (the click didn't land on anything inside it), so
          that's the standard check to tell it apart from a click on the
          dialog's own content. */}
      <dialog
        ref={dialogRef}
        onClose={() => setIsOpen(false)}
        onClick={(event) => {
          if (event.target === dialogRef.current) {
            handleClose();
          }
        }}
        className="rounded border border-zinc-200 p-4 backdrop:bg-black/40 dark:border-zinc-700 dark:bg-zinc-900"
      >
        {isOpen ? (
          <div className="flex flex-col gap-2">
            <h2 className="font-semibold">{service.name}</h2>
            {service.description !== null ? (
              <p className="text-sm text-zinc-600 dark:text-zinc-400">{service.description}</p>
            ) : null}
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {service.duration_minutes} хв
            </p>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">{service.price}</p>
            {service.category !== undefined ? (
              <p className="text-sm text-zinc-600 dark:text-zinc-400">{service.category.name}</p>
            ) : null}
            <button type="button" onClick={handleClose} className="self-end text-sm underline">
              Закрити
            </button>
          </div>
        ) : null}
      </dialog>
    </>
  );
}
