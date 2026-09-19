/**
 * Shape of `GET auth/me/`'s response body (docs/DECISIONS.md § "`/me/`
 * endpoint (Stage 12)", widened in § Stage 15 planning, item 7 to add
 * `name`/`phone`). Shared between AuthContext and anything that reads it,
 * rather than redeclared per file — it used to be a page-local interface
 * inside app/login/page.tsx only.
 *
 * `name`/`phone` are `null`, never `""`, when the Account has no linked
 * Customer yet (backend/accounts/serializers.py's `MeSerializer` -- a real,
 * not-rare state per item 5's recon).
 */
export interface Me {
  email: string;
  role: "admin" | "client";
  name: string | null;
  phone: string | null;
}
