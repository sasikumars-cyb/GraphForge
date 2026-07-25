import "@testing-library/jest-dom/vitest";

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
  globalThis.IntersectionObserver = MockIntersectionObserver as unknown as typeof IntersectionObserver;
}
