import { headers } from "next/headers";

import { SALON_SLUG_HEADER } from "@/middleware";

// Placeholder home page. The middleware resolves the active salon slug from the
// request's subdomain and passes it down as the x-salon-slug request header
// (null on the apex / an unrecognized host). Real salon-page and apex
// landing-page content lands in the frontend polish stages.
export default async function Home() {
  const slug = (await headers()).get(SALON_SLUG_HEADER);

  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
      <h1 className="text-2xl font-semibold">SalonHub</h1>
      {slug === null ? (
        <p className="text-zinc-600 dark:text-zinc-400">
          The platform is still in development.
        </p>
      ) : (
        <p className="text-zinc-600 dark:text-zinc-400">
          You&rsquo;re viewing {slug}&rsquo;s page — booking coming soon.
        </p>
      )}
    </main>
  );
}
