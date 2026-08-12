# Accessibility Baseline (KAN-38)

**Status:** Automated regression coverage established. Not a full WCAG AA audit or a VPAT — see Scope below.

## What this is

The product follows real accessibility conventions in code (semantic elements, color+text pairing on badges, real `disabled` attributes), but until this change nothing in the automated test suite enforced them — a regression could ship undetected. This adds [`jest-axe`](https://github.com/nickcolley/jest-axe) (axe-core under the hood) to the existing Vitest/Testing Library suite, wired so any component test can assert `expect(await axe(container)).toHaveNoViolations()`.

## What's covered

Six pages and one shared component now have a dedicated a11y regression test asserting zero axe-core violations once fully loaded (page names current as of the last documentation pass — grep `jest-axe\|toHaveNoViolations` under `frontend/src` for the authoritative, always-current list):

- `MissionControlPage` (renamed from `ControlCenterPage`)
- `WorkflowPage` (the core planning/development/testing/review pipeline view)
- `PullRequestDetailPage` (PR analysis)
- `ArchitecturePage`
- `DependencyQueryPage`
- `ImpactAnalysisPage`
- `Treemap` (shared component, exercised directly rather than through one page)

All pass with zero violations as of this change. `EvidencePanel`, `ReasoningLogPanel`, `ConfidenceBadge`, and the other shared agent-output components rendered inside them are exercised transitively by these tests.

## Scope — what this is *not*

- **Not exhaustive page coverage.** A handful of ~15+ pages have a dedicated a11y assertion. Extending this to the remaining pages is straightforward (the same `axe(container)` pattern) but wasn't attempted wholesale here — each additional page is a small, independent addition, not a prerequisite for the existing ones to be valid.
- **Not a full WCAG AA audit.** axe-core catches a meaningful, well-established subset of WCAG 2.x issues (missing labels, contrast ratios it can compute, ARIA misuse, landmark structure, etc.) but cannot verify things that need a human or a screen reader session — keyboard-trap testing under real focus management, meaningful reading order, or genuine screen-reader usability. Passing axe-core is a floor, not a ceiling.
- **Not a VPAT.** A Voluntary Product Accessibility Template is a formal conformance statement, typically produced against a specific WCAG version/level after a structured audit (often involving assistive-technology testing beyond what an automated tool alone can certify). This baseline is the automated-regression prerequisite a VPAT effort would build on, not the VPAT itself.

## How to extend

Any component or page test can add:

```ts
import { axe } from "jest-axe";

it("has no detectable accessibility violations", async () => {
  const { container } = render(<Thing />);
  await screen.findByText("something that confirms it's loaded");
  expect(await axe(container)).toHaveNoViolations();
});
```

`toHaveNoViolations()` is registered globally in `src/test/setup.ts` (`expect.extend(toHaveNoViolations)`); the vitest `Assertion` type augmentation lives in `src/test/axe.d.ts` (jest-axe ships its matcher's types for Jest's global `expect`, not vitest's, so this project needed its own).

## CI

These are ordinary Vitest tests — `npm run test` already runs them, so a violation fails the suite exactly like any other assertion. No separate CI step was added or is needed.
