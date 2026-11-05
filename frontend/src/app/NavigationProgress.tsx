"use client";

/**
 * Site-wide navigation progress (docs/DECISIONS.md § Stage 17 sub-step
 * order, 17.6c). A capture-phase click listener starts the pending state
 * for tracked link clicks (see shouldTrackClick), so it runs before
 * next/link's own handler; useNavigatingRouter starts it for programmatic
 * push/replace. A pathname or search-params change ends it. While the
 * pending state is visible (after 150 ms, see pendingNavigation) this
 * renders a thin neutral top bar and sets `<html data-navigating>`.
 * Back/forward does not start it.
 *
 * The bar imitates progress (decided 05.11.2026): `data-state="running"`
 * grows fast to ~30%, then slows towards ~90% and never finishes on its own;
 * when the navigation ends it switches to `data-state="finishing"`, fills
 * to 100% and fades for FINISH_MS, then is removed. The motion is pure CSS
 * (globals.css, `.navigation-progress`); reduced motion gets a static bar.
 * `<html data-navigating>` follows the pending state, so it goes at once,
 * not after the fade.
 *
 * Mounted in the root layout inside Suspense, because useSearchParams
 * needs a Suspense boundary for statically rendered routes.
 */

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";

import { end, isVisible, start, subscribe } from "@/lib/navigation/pendingNavigation";
import { shouldTrackClick } from "@/lib/navigation/shouldTrackClick";

// Matches the fade duration in globals.css.
const FINISH_MS = 200;

function getServerSnapshot(): boolean {
  return false;
}

export default function NavigationProgress() {
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const visible = useSyncExternalStore(subscribe, isVisible, getServerSnapshot);

  // Finishing starts when a visible bar stops being visible, and is
  // cancelled if a new navigation becomes visible meanwhile. Derived during
  // render from the previous value, not in an effect.
  const [finishing, setFinishing] = useState(false);
  const [wasVisible, setWasVisible] = useState(visible);
  if (visible !== wasVisible) {
    setWasVisible(visible);
    setFinishing(!visible);
  }

  useEffect(() => {
    if (!finishing) {
      return;
    }
    const timer = setTimeout(() => setFinishing(false), FINISH_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [finishing]);

  useEffect(() => {
    function onClick(event: MouseEvent) {
      const anchor = event.target instanceof Element ? event.target.closest("a") : null;
      if (anchor !== null && shouldTrackClick(event, anchor, window.location.href)) {
        start();
      }
    }
    document.addEventListener("click", onClick, true);
    return () => {
      document.removeEventListener("click", onClick, true);
    };
  }, []);

  useEffect(() => {
    end();
  }, [pathname, search]);

  useEffect(() => {
    const root = document.documentElement;
    if (visible) {
      root.setAttribute("data-navigating", "");
    } else {
      root.removeAttribute("data-navigating");
    }
    return () => {
      root.removeAttribute("data-navigating");
    };
  }, [visible]);

  if (!visible && !finishing) {
    return null;
  }
  return (
    <div
      aria-hidden="true"
      data-state={visible ? "running" : "finishing"}
      className="navigation-progress fixed inset-x-0 top-0 z-50 h-0.5 bg-neutral-400"
    />
  );
}
