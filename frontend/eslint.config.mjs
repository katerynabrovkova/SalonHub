import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // 17.6c (docs/DECISIONS.md § Stage 17 sub-step order): every push/replace
  // goes through useNavigatingRouter so it starts the navigation progress.
  {
    ignores: ["src/lib/navigation/useNavigatingRouter.ts"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "next/navigation",
              importNames: ["useRouter"],
              message:
                "Use useNavigatingRouter from @/lib/navigation/useNavigatingRouter, so the navigation progress starts.",
            },
          ],
        },
      ],
    },
  },
  // docs/DECISIONS.md § Frontend › "Site language" / "API client": every
  // server-side backend URL is built by internalApiUrl, which always adds the
  // site language, so no other file reads INTERNAL_API_URL. Assigning it (a
  // test setting the env var) is not a read and stays allowed.
  {
    ignores: ["src/lib/api/internalApiUrl.ts", "src/lib/api/internalApiUrl.test.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "MemberExpression[object.object.name='process'][object.property.name='env'][property.name='INTERNAL_API_URL']:not(AssignmentExpression > MemberExpression.left)",
          message: "Build server-side backend URLs with internalApiUrl from @/lib/api/internalApiUrl.",
        },
        {
          selector:
            "MemberExpression[object.object.name='process'][object.property.name='env'][property.value='INTERNAL_API_URL']:not(AssignmentExpression > MemberExpression.left)",
          message: "Build server-side backend URLs with internalApiUrl from @/lib/api/internalApiUrl.",
        },
        {
          selector:
            "VariableDeclarator[init.object.name='process'][init.property.name='env'] > ObjectPattern > Property[key.name='INTERNAL_API_URL']",
          message: "Build server-side backend URLs with internalApiUrl from @/lib/api/internalApiUrl.",
        },
      ],
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
