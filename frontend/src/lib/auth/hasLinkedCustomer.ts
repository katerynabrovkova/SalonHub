/**
 * Derives "does this Account have a linked Customer yet" from `Me` (Stage 15
 * item 5, Cycle D). `MeSerializer` returns `name`/`phone` as `null` only when
 * no Customer is linked (docs/DECISIONS.md § "Backend + frontend: Account/
 * Customer name and phone in the profile panel") -- an explicit `!== null`
 * check, not truthiness, since a linked Customer with an empty-string name is
 * still linked.
 */
import type { Me } from "./Me";

export function hasLinkedCustomer(me: Me): boolean {
  return me.name !== null;
}
