/**
 * The message for a guest link that cannot be used (missing, malformed,
 * tampered or expired token), shared by `/booking/pay` and the guest manage
 * page so both say the same thing (docs/DECISIONS.md "Frontend > Guest manage
 * page"). Every token failure is the same 400 on the backend, so the pages do
 * not tell the cases apart either.
 */
export const INVALID_GUEST_LINK_MESSAGE = "Посилання недійсне або застаріле.";
