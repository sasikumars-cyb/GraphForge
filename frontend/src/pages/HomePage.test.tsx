import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HomePage } from "./HomePage";

describe("HomePage", () => {
  it("renders the scaffold confirmation heading", () => {
    render(<HomePage />);

    expect(
      screen.getByRole("heading", { name: /project scaffold is running/i }),
    ).toBeInTheDocument();
  });
});
