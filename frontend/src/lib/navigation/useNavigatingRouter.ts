"use client";

// The one place allowed to import useRouter from next/navigation: the
// no-restricted-imports rule in eslint.config.mjs exempts only this file.
import { useRouter } from "next/navigation";
import { useMemo } from "react";

import { start } from "./pendingNavigation";
import { isRouteChange } from "./shouldTrackClick";

type Router = ReturnType<typeof useRouter>;

/**
 * `useRouter` whose `push`/`replace` call `start()` first when the target
 * differs from the current URL (docs/DECISIONS.md § Stage 17 sub-step order,
 * 17.6c). The current URL comes from `window.location`, not usePathname /
 * useSearchParams, so call-site tests that mock next/navigation with only
 * `useRouter` keep working. Arguments are passed through unchanged, and the
 * router methods are looked up only when called, since those mocks may
 * provide only one of them.
 */
export function useNavigatingRouter(): Router {
  const router = useRouter();
  return useMemo<Router>(
    () => ({
      ...router,
      push: (...args: Parameters<Router["push"]>) => {
        startIfRouteChange(args[0]);
        return router.push(...args);
      },
      replace: (...args: Parameters<Router["replace"]>) => {
        startIfRouteChange(args[0]);
        return router.replace(...args);
      },
    }),
    [router],
  );
}

function startIfRouteChange(href: string): void {
  if (isRouteChange(href, window.location.href)) {
    start();
  }
}
