import { ApiError } from "@/lib/api/errors";

/**
 * Shared error handling for "the specialist must perform the service"
 * (docs/DECISIONS.md § "Fix: the specialist must perform the service").
 * Used by booking step 3 (availability errors) and by both step-4 forms, so
 * none of them checks error codes inline.
 */

export type Step3ErrorKind = "specialist_not_offering" | "service_unavailable" | "other";

export function isSpecialistNotOfferingError(err: unknown): boolean {
  return err instanceof ApiError && err.code === "SPECIALIST_DOES_NOT_OFFER_SERVICE";
}

/**
 * A service error is recognised by validation details on the `service`
 * field: an inactive, unknown or other-salon service id all land there.
 * Any other failure (another field, 5xx, network) is "other".
 */
export function classifyStep3Error(err: unknown): Step3ErrorKind {
  if (isSpecialistNotOfferingError(err)) {
    return "specialist_not_offering";
  }
  if (err instanceof ApiError && "service" in err.details) {
    return "service_unavailable";
  }
  return "other";
}

/** Step 2 for this service. Always `entry=service`, even when the customer
 * started from a specialist, since that specialist is the problem. */
export function buildChooseAnotherSpecialistUrl(service: string): string {
  return `/booking?entry=service&service=${service}&step=2`;
}
