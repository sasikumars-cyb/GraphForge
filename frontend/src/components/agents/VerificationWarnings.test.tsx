import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VerificationWarnings } from "./VerificationWarnings";
import { PlanningResultDetails } from "./StageResultDetails";
import type { PlanningResult } from "../../types/agent";

describe("VerificationWarnings", () => {
  it("renders nothing when there are no warnings", () => {
    const { container } = render(<VerificationWarnings warnings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the field is absent", () => {
    // Older runs predate the field entirely — absence must read as "no
    // warnings", never as an empty amber panel implying a check ran.
    const { container } = render(<VerificationWarnings />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists every warning with a count and the subject", () => {
    render(
      <VerificationWarnings
        warnings={[
          "File 'src/pipeline/manifest_processor.py' claimed for 'soco-ingest' does not appear in this run's indexed component data — unverified.",
          "Affected component 'pipeline.validation.schema_validator' does not appear in this run's graph traversal results — unverified.",
        ]}
        subject="plan"
      />,
    );

    expect(screen.getByText("2 unverified claims in this plan")).toBeInTheDocument();
    expect(screen.getByText(/manifest_processor\.py/)).toBeInTheDocument();
    expect(screen.getByText(/schema_validator/)).toBeInTheDocument();
  });

  it("uses singular wording for a single warning", () => {
    render(<VerificationWarnings warnings={["only one"]} subject="test plan" />);
    expect(screen.getByText("1 unverified claim in this test plan")).toBeInTheDocument();
  });

  it("surfaces warnings carried on a real planning result", () => {
    // Regression test for the gap this component closes: the backend has
    // always computed `verification_warnings` (four agents produce it), and
    // the frontend rendered it nowhere — `grep verification_warnings
    // frontend/src/` returned no hits at all, including in the types. A
    // planning result could therefore show a green "verified" badge beside
    // a fabricated file path with nothing on screen to contradict it.
    const result = {
      executive_summary: "Chunk the manifest.",
      implementation_steps: [],
      affected_components: [],
      kafka_topics_involved: [],
      risk_considerations: [],
      graph_context_used: true,
      verification_warnings: [
        "Affected component 'trnasform_manifest_logger_job' does not appear in this run's graph traversal results — unverified.",
      ],
    } as unknown as PlanningResult;

    render(<PlanningResultDetails result={result} />);

    expect(screen.getByText("1 unverified claim in this plan")).toBeInTheDocument();
    expect(screen.getByText(/trnasform_manifest_logger_job/)).toBeInTheDocument();
  });
});
