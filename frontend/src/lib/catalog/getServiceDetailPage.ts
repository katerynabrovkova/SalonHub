/**
 * Server-safe data fetch for the /services/[id] detail page (docs/DECISIONS.md
 * § Stage 13 amendment — service detail page). Pure function: no
 * document.cookie, no import from api/client.ts — safe to call from a Server
 * Component, unlike apiRequest() which is browser-only. Mirrors
 * getServicesPage.ts's conventions.
 */

import type { Service } from "./getServicesPage";

export interface Specialist {
  id: number;
  salon: number;
  name: string;
  bio: string;
  is_active: boolean;
  services: number[];
  created_at: string;
  updated_at: string;
}

interface DrfPage<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface ServiceDetailPage {
  service: Service;
  specialists: Specialist[];
}

export async function getServiceDetailPage(
  slug: string,
  id: number,
): Promise<ServiceDetailPage | null> {
  const apiBase = process.env.INTERNAL_API_URL;

  const serviceResponse = await fetch(`${apiBase}/api/v1/salons/${slug}/services/${id}/`);
  if (serviceResponse.status === 404) {
    return null;
  }
  if (!serviceResponse.ok) {
    throw new Error(`Failed to fetch service ${id} for salon ${slug}`);
  }
  const service = (await serviceResponse.json()) as Service;

  const specialistsResponse = await fetch(
    `${apiBase}/api/v1/salons/${slug}/specialists/?service=${id}`,
  );
  if (!specialistsResponse.ok) {
    throw new Error(`Failed to fetch specialists for service ${id}, salon ${slug}`);
  }
  const specialistsPage = (await specialistsResponse.json()) as DrfPage<Specialist>;

  return { service, specialists: specialistsPage.results };
}
