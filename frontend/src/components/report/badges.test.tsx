import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReadinessBadge, SynthesisStatusBadge, VerificationStatusBadge } from "./badges";

describe("SynthesisStatusBadge", () => {
  it.each([
    ["supported", "Supported"],
    ["inferred", "Inferred"],
    ["contradicted", "Contradicted"],
    ["unknown", "Unknown"],
  ] as const)("renders %s as %s", (status, label) => {
    render(<SynthesisStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe("VerificationStatusBadge", () => {
  it.each([
    ["verified", "Verified"],
    ["unverified", "Unverified"],
    ["not_checked", "Not checked"],
  ] as const)("renders %s as %s", (status, label) => {
    render(<VerificationStatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("renders null as Not checked, never inferring VERIFIED/UNVERIFIED", () => {
    render(<VerificationStatusBadge status={null} />);
    expect(screen.getByText("Not checked")).toBeInTheDocument();
  });
});

describe("ReadinessBadge", () => {
  it.each([
    ["ready", "Ready for approval"],
    ["needs_revision", "Needs revision"],
    ["not_ready", "Not ready for approval"],
    ["unknown", "Readiness unknown"],
  ] as const)("renders %s as %s", (readiness, label) => {
    render(<ReadinessBadge readiness={readiness} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});
