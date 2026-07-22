import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Card } from "./Card";

describe("Card", () => {
  it("renders a title, description, and children", () => {
    render(
      <Card title="Recent pull requests" description="Latest changes">
        <p>Body content</p>
      </Card>,
    );

    expect(screen.getByText("Recent pull requests")).toBeInTheDocument();
    expect(screen.getByText("Latest changes")).toBeInTheDocument();
    expect(screen.getByText("Body content")).toBeInTheDocument();
  });

  it("renders without a header when no title or description is given", () => {
    render(<Card>Just content</Card>);
    expect(screen.getByText("Just content")).toBeInTheDocument();
  });
});
