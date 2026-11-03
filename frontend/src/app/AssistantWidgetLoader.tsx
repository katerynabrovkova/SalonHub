import { headers } from "next/headers";

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
 * `lang=uk` matches the interface language (`<html lang="uk">` in
 * layout.tsx), so the salon name in the widget header is Ukrainian.
 */
export default async function AssistantWidgetLoader() {
  const slug = (await headers()).get(SALON_SLUG_HEADER);
  if (slug === null) {
    return null;
  }

  let salon: SalonInfo;
  try {
    salon = await getSalonInfoPage(slug, "uk");
  } catch {
    return null;
  }

  if (!salon.assistant_enabled) {
    return null;
  }

  return <AssistantWidget salonName={salon.name} />;
}
