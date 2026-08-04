import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, vi } from "vitest";
import { toHaveNoViolations } from "jest-axe";

// KAN-38 — makes `expect(await axe(container)).toHaveNoViolations()`
// available in every component test file without a per-file import; see
// src/test/axe.d.ts for the corresponding vitest Assertion type
// augmentation (jest-axe ships its matcher's types for Jest's global
// `expect`, not vitest's).
expect.extend(toHaveNoViolations);

// Unit tests must never reach a real network — every API call a
// component makes is expected to go through a mocked `lib/api/*`
// function (`vi.spyOn(someApi, "someFunction")`). Without this guard, a
// component with an incompletely-mocked call silently falls through to
// the real `fetch`, and — if a real backend happens to be reachable at
// API_BASE_URL in this environment (e.g. a dev server left running) —
// gets a real response instead of a test failure. That's exactly what
// caused an intermittent, hard-to-diagnose failure in
// PullRequestDetailPage.test.tsx: an unmocked call reached a live
// backend, got a genuine 401 for the test's fake token, and the
// resulting UNAUTHORIZED_EVENT logged out whatever test happened to be
// rendering when the real response eventually arrived. This turns that
// class of bug into an immediate, attributable failure at the actual
// unmocked call site instead of a delayed, misattributed one.
const originalFetch = globalThis.fetch;

beforeEach(() => {
  // A rejected Promise, not a synchronous throw — real `fetch()` never
  // throws synchronously (network failures always reject), so a
  // synchronous throw here can break a call site that (correctly, for
  // real fetch semantics) only wraps the *awaited* result in a
  // try/catch, not the `fetch(...)` call expression itself.
  globalThis.fetch = vi.fn(() =>
    Promise.reject(
      new Error(
        "Unexpected real network call from a unit test — mock the specific " +
          "lib/api/* function this component calls instead of letting it " +
          "reach fetch() directly.",
      ),
    ),
  ) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

// jsdom doesn't implement IntersectionObserver. Components that use it
// (e.g. BlueprintExplorer's scroll-spy) throw at mount without this —
// previously latent since nothing under test rendered one.
if (typeof globalThis.IntersectionObserver === "undefined") {
  // Not `implements IntersectionObserver` — that interface's exact shape
  // (e.g. `scrollMargin`) varies across TS lib versions; this only needs
  // to satisfy components that call observe/unobserve/disconnect, so it's
  // deliberately loose and cast at the assignment below instead.
  class MockIntersectionObserver {
    readonly root: Element | Document | null = null;
    readonly rootMargin: string = "";
    readonly thresholds: ReadonlyArray<number> = [];
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  globalThis.IntersectionObserver =
    MockIntersectionObserver as unknown as typeof IntersectionObserver;
}
