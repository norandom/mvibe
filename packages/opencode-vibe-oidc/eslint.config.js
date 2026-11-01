import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default [
  { ignores: ["dist/**", ".host/**", ".compat/**", "test/tool-compatibility/.task43-debug-*.ts"] },
  {
    files: ["src/**/*.ts", "scripts/**/*.ts", "test/**/*.ts"],
    languageOptions: { parser: tseslint.parser, ecmaVersion: "latest", sourceType: "module" },
    rules: {
      ...js.configs.recommended.rules,
      // TypeScript checks names/types. Its parser needs the TS-aware unused rule;
      // that style rule is intentionally outside this correctness-only baseline.
      "no-undef": "off",
      "no-unused-vars": "off",
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
  {
    files: ["test/tool-compatibility/runner.ts"],
    // An unretired owned process tree must fail the probe, even after another failure.
    rules: { "no-unsafe-finally": "off" },
  },
  {
    files: ["test/tool-compatibility/observe.ts"],
    // Redaction errors must discard the original exception, including its cause.
    rules: { "preserve-caught-error": "off" },
  },
];
