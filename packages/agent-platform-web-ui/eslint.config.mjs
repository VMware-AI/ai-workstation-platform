import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // esbuild output of `npm run build:worker` (#229) — a build artifact.
    "dist/**",
  ]),
  {
    rules: {
      // React-Compiler rule (react-hooks v6) that fires on the idiomatic
      // fetch-on-mount / polling pattern used across every dashboard page
      // (useEffect calling an async loader that setStates after await). There
      // is no clean per-call fix; properly resolving it means moving to a data
      // layer (react-query/SWR) — tracked separately. Keep it as a warning so
      // it stays visible without blocking `next build`.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
]);

export default eslintConfig;
