import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { RepositoryPickerList } from "./RepositoryPickerList";
import type { AvailableRepository } from "../types/github";

// jsdom never lays anything out — every element's real height is 0,
// which `@tanstack/react-virtual` reads as "the scroll viewport is 0px
// tall" and (correctly, given that premise) mounts zero rows in range.
// Scoped to this file only: give the scroll container a plausible fixed
// height so the virtualizer behaves the way it does in a real browser
// (matches `max-h-72` = 18rem = 288px in RepositoryPickerList.tsx).
let originalGetBoundingClientRect: typeof HTMLElement.prototype.getBoundingClientRect;
beforeEach(() => {
  originalGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;
  HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
    return { ...originalGetBoundingClientRect.call(this), height: 288, width: 400 } as DOMRect;
  };
});
afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = originalGetBoundingClientRect;
});

function repo(n: number, overrides: Partial<AvailableRepository> = {}): AvailableRepository {
  return {
    provider_repo_id: String(n),
    owner: "acme",
    name: `repo-${n}`,
    full_name: `acme/repo-${n}`,
    private: n % 2 === 0,
    default_branch: "main",
    html_url: `https://github.com/acme/repo-${n}`,
    is_selected: false,
    ...overrides,
  };
}

// Simple controlled-state wrapper so onChangeSelected actually updates what
// re-renders — the real component (GitHubIntegrationCard) owns selectedIds
// the same way.
function ControlledPicker({ repos }: { repos: AvailableRepository[] }) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  return (
    <RepositoryPickerList repos={repos} selectedIds={selectedIds} onChangeSelected={setSelectedIds} />
  );
}

describe("RepositoryPickerList", () => {
  it("filters repositories by full_name as the user types", async () => {
    const user = userEvent.setup();
    const repos = [repo(1, { full_name: "acme/order-service" }), repo(2, { full_name: "acme/billing" })];
    render(<ControlledPicker repos={repos} />);

    expect(screen.getByRole("checkbox", { name: /order-service/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /billing/ })).toBeInTheDocument();

    await user.type(screen.getByLabelText("Search repositories"), "order");

    expect(screen.getByRole("checkbox", { name: /order-service/ })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /billing/ })).not.toBeInTheDocument();
  });

  it("shows a no-matches message when the search finds nothing", async () => {
    const user = userEvent.setup();
    render(<ControlledPicker repos={[repo(1), repo(2)]} />);

    await user.type(screen.getByLabelText("Search repositories"), "does-not-exist");

    expect(screen.getByText(/No repositories match/)).toBeInTheDocument();
  });

  it("select-all checkbox selects every currently-visible repository", async () => {
    const user = userEvent.setup();
    render(<ControlledPicker repos={[repo(1), repo(2), repo(3)]} />);

    const selectAll = screen.getByRole("checkbox", { name: "Select all repositories" });
    expect(selectAll).not.toBeChecked();

    await user.click(selectAll);

    expect(screen.getByRole("checkbox", { name: /repo-1/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /repo-2/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /repo-3/ })).toBeChecked();
  });

  it("select-all clicked again deselects everything it selected", async () => {
    const user = userEvent.setup();
    render(<ControlledPicker repos={[repo(1), repo(2)]} />);

    const selectAll = screen.getByRole("checkbox", { name: "Select all repositories" });
    await user.click(selectAll);
    await user.click(selectAll);

    expect(screen.getByRole("checkbox", { name: /repo-1/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /repo-2/ })).not.toBeChecked();
  });

  it("select-all only affects repositories matching the current search", async () => {
    const user = userEvent.setup();
    const repos = [
      repo(1, { full_name: "acme/order-service" }),
      repo(2, { full_name: "acme/billing" }),
    ];
    render(<ControlledPicker repos={repos} />);

    await user.type(screen.getByLabelText("Search repositories"), "order");
    await user.click(screen.getByRole("checkbox", { name: "Select all matching repositories" }));

    // Clear the search to see the full list again — the unmatched repo
    // must not have been touched by a select-all scoped to the filter.
    await user.clear(screen.getByLabelText("Search repositories"));

    expect(screen.getByRole("checkbox", { name: /order-service/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /billing/ })).not.toBeChecked();
  });

  it("shows the selected count and total", async () => {
    const user = userEvent.setup();
    render(<ControlledPicker repos={[repo(1), repo(2), repo(3)]} />);

    expect(screen.getByText("0 selected · 3 repositories")).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /repo-1/ }));

    expect(screen.getByText("1 selected · 3 repositories")).toBeInTheDocument();
  });

  it("switches to the virtualized path above the threshold and reserves the full scroll height", async () => {
    const user = userEvent.setup();
    const repos = Array.from({ length: 500 }, (_, i) => repo(i + 1));
    render(<ControlledPicker repos={repos} />);

    // The virtualizer's total-size element reserves scroll space for
    // every row up front (this is what makes scrolling to row 499 work
    // correctly) even though far fewer than 500 rows are ever actually
    // mounted at once — the mounted count itself depends on jsdom's
    // (non-real) layout measurements, so it isn't asserted on here; the
    // total-size math is the layout-independent part of "virtualization
    // is active and correct."
    const totalSizeEl = screen.getByTestId("repository-list-total-size");
    const height = Number(totalSizeEl.style.height.replace("px", ""));
    expect(height).toBeGreaterThan(500 * 40); // >= ~row-height * count

    expect(screen.getByText("0 selected · 500 repositories")).toBeInTheDocument();

    // Search still works at this scale — filtering drops well below the
    // virtualization threshold, so this also exercises the plain-render
    // path within the same large-list scenario. "499" isn't a substring
    // of any other repo number in 1-500, so this is an unambiguous
    // single match — `getByRole` itself would throw on more than one.
    await user.type(screen.getByLabelText("Search repositories"), "repo-499");
    expect(screen.getByRole("checkbox", { name: "acme/repo-499" })).toBeInTheDocument();
    expect(screen.getByText("0 selected · 1 of 500 repositories")).toBeInTheDocument();
  });

  it("marks private repositories with a badge", () => {
    render(<ControlledPicker repos={[repo(2, { private: true, full_name: "acme/secret" })]} />);
    expect(screen.getByText("Private")).toBeInTheDocument();
  });
});
