/**
 * A review count with its Ukrainian plural form (docs/DECISIONS.md § "Stage
 * 17 sub-step order", 17.6-pre): "1 відгук", "2 відгуки", "5 відгуків",
 * "21 відгук", "11 відгуків". Shared by SpecialistCard and the assistant
 * card. Intl.PluralRules("uk") gives the categories: one (1, 21, 31…),
 * few (2-4, 22-24…) and many (0, 5-20, 25-30…, and 11-14).
 */

const RULES = new Intl.PluralRules("uk");

const FORMS: Partial<Record<Intl.LDMLPluralRule, string>> = {
  one: "відгук",
  few: "відгуки",
};

export function reviewCount(count: number): string {
  return `${count} ${FORMS[RULES.select(count)] ?? "відгуків"}`;
}
