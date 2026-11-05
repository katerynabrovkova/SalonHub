/**
 * Pure classifier for the navigation-progress click listener
 * (docs/DECISIONS.md § Stage 17 sub-step order, 17.6c). Tracks plain left
 * clicks on same-origin links to a different URL. Ignores modifier and
 * middle clicks, a target other than `_self`, `download`, external links,
 * and links that change only the hash or nothing at all — none of those
 * produce a route change that would end the pending state.
 */
export function shouldTrackClick(
  event: MouseEvent,
  anchor: HTMLAnchorElement,
  currentHref: string,
): boolean {
  if (event.button !== 0) {
    return false;
  }
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
    return false;
  }
  const target = anchor.getAttribute("target");
  if (target !== null && target !== "" && target !== "_self") {
    return false;
  }
  if (anchor.hasAttribute("download")) {
    return false;
  }
  const href = anchor.getAttribute("href");
  if (href === null) {
    return false;
  }
  return isRouteChange(href, currentHref);
}

/**
 * True when `href`, resolved against `currentHref`, is same-origin and
 * differs in pathname or search. Shared with useNavigatingRouter.
 */
export function isRouteChange(href: string, currentHref: string): boolean {
  let next: URL;
  let current: URL;
  try {
    current = new URL(currentHref);
    next = new URL(href, current);
  } catch {
    return false;
  }
  if (next.origin !== current.origin) {
    return false;
  }
  return next.pathname !== current.pathname || next.search !== current.search;
}
