/**
 * Initials avatar-fallback text for a translated display name (up to two
 * words, uppercased first letters). Shared by specialists/page.tsx and
 * services/page.tsx, both of which fall back to this when the entity has no
 * photo (docs/DECISIONS.md § Stage 13 reopened: implementation decisions
 * for the popup, category fetch, and detail-page removal).
 */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}
