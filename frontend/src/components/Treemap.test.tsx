import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { axe } from "jest-axe";
import { Treemap, type TreemapItem } from "./Treemap";
import { computeTreemapLayout } from "./treemapLayout";

describe("computeTreemapLayout", () => {
  it("conserves total area across every rect", () => {
    const items: TreemapItem[] = [
      { id: "a", label: "A", value: 50 },
      { id: "b", label: "B", value: 30 },
      { id: "c", label: "C", value: 20 },
    ];

    const rects = computeTreemapLayout(items, 0, 0, 400, 200);

    const totalArea = rects.reduce((sum, r) => sum + r.width * r.height, 0);
    expect(totalArea).toBeCloseTo(400 * 200, 0);
  });

  it("gives each item area proportional to its value, not its rank", () => {
    const items: TreemapItem[] = [
      { id: "big", label: "Big", value: 90 },
      { id: "small", label: "Small", value: 10 },
    ];

    const rects = computeTreemapLayout(items, 0, 0, 400, 200);
    const big = rects.find((r) => r.item.id === "big")!;
    const small = rects.find((r) => r.item.id === "small")!;

    expect(big.width * big.height).toBeCloseTo((90 / 100) * 400 * 200, -1);
    expect(small.width * small.height).toBeCloseTo((10 / 100) * 400 * 200, -1);
  });

  it("drops zero/negative-value items rather than laying out a zero-area rect", () => {
    const items: TreemapItem[] = [
      { id: "a", label: "A", value: 10 },
      { id: "empty", label: "Empty", value: 0 },
      { id: "negative", label: "Negative", value: -5 },
    ];

    const rects = computeTreemapLayout(items, 0, 0, 400, 200);

    expect(rects.map((r) => r.item.id)).toEqual(["a"]);
  });

  it("returns nothing for an empty item list or a zero-size container", () => {
    expect(computeTreemapLayout([], 0, 0, 400, 200)).toEqual([]);
    expect(
      computeTreemapLayout([{ id: "a", label: "A", value: 10 }], 0, 0, 0, 200),
    ).toEqual([]);
  });

  it("produces no overlapping rects", () => {
    const items: TreemapItem[] = Array.from({ length: 6 }, (_, i) => ({
      id: `item-${i}`,
      label: `Item ${i}`,
      value: (i + 1) * 7,
    }));

    const rects = computeTreemapLayout(items, 0, 0, 500, 300);

    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i];
        const b = rects[j];
        const overlapsX = a.x < b.x + b.width && b.x < a.x + a.width;
        const overlapsY = a.y < b.y + b.height && b.y < a.y + a.height;
        expect(overlapsX && overlapsY).toBe(false);
      }
    }
  });
});

describe("Treemap", () => {
  const ITEMS: TreemapItem[] = [
    { id: "payments", label: "Payments", value: 30, sublabel: "3 repos" },
    { id: "checkout", label: "Checkout", value: 10, sublabel: "1 repo" },
  ];

  it("renders a labeled, clickable cell per item", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Treemap items={ITEMS} onSelect={onSelect} ariaLabel="Domains" />);

    const cell = screen.getByRole("button", { name: "Payments, 3 repos" });
    await user.click(cell);

    expect(onSelect).toHaveBeenCalledWith(ITEMS[0]);
  });

  it("is keyboard-activatable", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<Treemap items={ITEMS} onSelect={onSelect} ariaLabel="Domains" />);

    const cell = screen.getByRole("button", { name: "Payments, 3 repos" });
    cell.focus();
    await user.keyboard("{Enter}");

    expect(onSelect).toHaveBeenCalledWith(ITEMS[0]);
  });

  it("renders a disabled item as non-interactive", () => {
    const onSelect = vi.fn();
    render(
      <Treemap
        items={[{ id: "ungrouped", label: "Ungrouped", value: 5, disabled: true }]}
        onSelect={onSelect}
        ariaLabel="Domains"
      />,
    );

    expect(screen.queryByRole("button", { name: /Ungrouped/ })).not.toBeInTheDocument();
  });

  it("renders nothing for an empty item list", () => {
    const { container } = render(<Treemap items={[]} ariaLabel="Domains" />);
    expect(container.querySelector("svg")).not.toBeInTheDocument();
  });

  it("has no detectable accessibility violations", async () => {
    const { container } = render(
      <Treemap items={ITEMS} onSelect={vi.fn()} ariaLabel="Domains" />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
