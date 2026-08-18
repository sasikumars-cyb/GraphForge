import { describe, expect, it } from "vitest";
import { NAV_ITEMS, NAV_SECTIONS } from "./nav-items";

describe("nav-items", () => {
  it("includes a discoverable Engineering Tasks entry pointing at /engineering-tasks", () => {
    const item = NAV_ITEMS.find((i) => i.path === "/engineering-tasks");
    expect(item).toBeDefined();
    expect(item?.label).toBe("Engineering Tasks");
  });

  it("keeps Engineering Tasks in its own section, not folded into Build or Monitor", () => {
    const section = NAV_SECTIONS.find((s) =>
      s.items.some((i) => i.path === "/engineering-tasks"),
    );
    expect(section?.section).toBe("Engineering Tasks");
  });
});
