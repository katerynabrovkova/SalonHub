/**
 * Shape of `GET auth/me/`'s response body (docs/DECISIONS.md § "`/me/`
 * endpoint (Stage 12)"): just `email` + `role`, nothing else. Shared between
 * AuthContext and anything that reads it, rather than redeclared per file —
 * it used to be a page-local interface inside app/login/page.tsx only.
 */
export interface Me {
  email: string;
  role: "admin" | "client";
}
