/**
 * Renders the token-moving inline script (lib/security/fragmentTokenScript.ts,
 * docs/DECISIONS.md "Booking > Guest token"). Server Component only: the
 * script runs once, from the server HTML, on a full page load; React never
 * runs it again on hydration or client navigation.
 */
import {
  buildFragmentTokenScript,
  type FragmentTokenScriptConfig,
} from "@/lib/security/fragmentTokenScript";

export default function FragmentTokenScript({ config }: { config: FragmentTokenScriptConfig }) {
  return <script dangerouslySetInnerHTML={{ __html: buildFragmentTokenScript(config) }} />;
}
