import { headers } from "next/headers";

import { getSiteLanguage } from "@/lib/i18n/siteLanguage";
import { getSalonInfoPage, type SalonInfo } from "@/lib/tenants/getSalonInfoPage";
import { SALON_SLUG_HEADER } from "@/middleware";

import AssistantWidget from "./AssistantWidget";

/**
 * Server loader for the assistant widget, rendered by the root layout on
 * every page (docs/DECISIONS.md § "Stage 17 sub-step order", 17.3b).
 *
 * Renders nothing on a null slug (apex, www, unknown host), on any error
 * from the salon-info fetch (unknown salon, backend down) or when the salon
 * has the assistant off. The catch is what keeps a failed fetch here from
 * breaking every other page through the root layout. The slug comes only
 * from the middleware header, which is resolved from the host (17.3a).
 *
 * The salon info is fetched in the site language (`lib/i18n/siteLanguage.ts`,
 * the same value as `<html lang>` in layout.tsx), so the salon name in the
 * widget header matches the interface.
 */
export default async function AssistantWidgetLoader() {
  const slug = (await headers()).get(SALON_SLUG_HEADER);
  if (slug === null) {
    return null;
  }

  let salon: SalonInfo;
  try {
    salon = await getSalonInfoPage(slug, getSiteLanguage());
  } catch {
    return null;
  }

  if (!salon.assistant_enabled) {
    return null;
  }

  return (
    <AssistantWidget salonName={salon.name} slug={slug} starters={salon.assistant_starters} />
  );
}
