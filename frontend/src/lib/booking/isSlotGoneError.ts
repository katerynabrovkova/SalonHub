import { ApiError } from "@/lib/api/errors";

/**
 * Whether a failed booking POST means "this slot is gone — send the user back
 * to pick another one" (booking step 3). The backend has two codes for it
 * (backend `core/exceptions.py`):
 *
 * - `SLOT_NO_LONGER_AVAILABLE` (409): the slot was still offered but got taken
 *   between the offered-slot check and the insert (the narrow race).
 * - `SLOT_NOT_OFFERED` (400): the slot is no longer among the offered
 *   candidates at all — which is what a slot already taken by another booking
 *   produces, since availability subtracts existing appointments.
 *
 * Branches on the code, never the status alone: any other 400 (e.g. a
 * validation error) is not a slot problem.
 */
export function isSlotGoneError(err: unknown): boolean {
  return (
    err instanceof ApiError &&
    (err.code === "SLOT_NO_LONGER_AVAILABLE" || err.code === "SLOT_NOT_OFFERED")
  );
}
