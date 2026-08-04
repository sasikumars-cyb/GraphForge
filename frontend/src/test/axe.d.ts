import type { AxeResults } from "axe-core";

// jest-axe ships toHaveNoViolations' types for Jest's global `expect`
// (@types/jest-axe references "jest", not "vitest") - this project uses
// vitest, so the matcher is registered at runtime in setup.ts via
// expect.extend, but needs its own type augmentation here for
// `expect(results).toHaveNoViolations()` to type-check.
interface CustomMatchers<R = unknown> {
  toHaveNoViolations(): R;
}

declare module "vitest" {
  interface Assertion<T = AxeResults> extends CustomMatchers<T> {}
  interface AsymmetricMatchersContaining extends CustomMatchers {}
}
