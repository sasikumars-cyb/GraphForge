import type {
  Readiness,
  SynthesisStatus,
  VerificationStatus,
} from "../../lib/api/reports";
import { StatusBadge, type StatusTone } from "../StatusBadge";

// ---------------------------------------------------------------------------
// The three-concept badge vocabulary Report V2 requires (ADR 0024 §7):
// Confidence, Synthesis status, and Verification status are never rendered
// as one merged indicator, and never share the same visual channel — each
// gets its own badge with its own tone family. SUPPORTED != VERIFIED is
// enforced here, structurally: there is no code path that can collapse
// the two into a single value.
// ---------------------------------------------------------------------------

const SYNTHESIS_STATUS_LABEL: Record<SynthesisStatus, string> = {
  supported: "Supported",
  inferred: "Inferred",
  contradicted: "Contradicted",
  unknown: "Unknown",
};

const SYNTHESIS_STATUS_TONE: Record<SynthesisStatus, StatusTone> = {
  supported: "success",
  inferred: "info",
  contradicted: "danger",
  unknown: "neutral",
};

/** "What did the reasoning conclude about this specific claim?" — never
 * "was this code-checked" (see VerificationStatusBadge). */
export function SynthesisStatusBadge({ status }: { status: SynthesisStatus }) {
  return (
    <StatusBadge label={SYNTHESIS_STATUS_LABEL[status]} tone={SYNTHESIS_STATUS_TONE[status]} />
  );
}

const VERIFICATION_STATUS_LABEL: Record<VerificationStatus, string> = {
  verified: "Verified",
  unverified: "Unverified",
  not_checked: "Not checked",
};

const VERIFICATION_STATUS_TONE: Record<VerificationStatus, StatusTone> = {
  verified: "success",
  unverified: "warning",
  not_checked: "neutral",
};

/** "Did a deterministic code check confirm this?" — `null` (no correlated
 * Knowledge Ledger row) renders as "Not checked", the honest reading of
 * "no verification exists for this claim", never inferred from confidence
 * or synthesis status. */
export function VerificationStatusBadge({ status }: { status: VerificationStatus | null }) {
  const value = status ?? "not_checked";
  return (
    <StatusBadge label={VERIFICATION_STATUS_LABEL[value]} tone={VERIFICATION_STATUS_TONE[value]} />
  );
}

const READINESS_LABEL: Record<Readiness, string> = {
  ready: "Ready for approval",
  needs_revision: "Needs revision",
  not_ready: "Not ready for approval",
  unknown: "Readiness unknown",
};

const READINESS_TONE: Record<Readiness, StatusTone> = {
  ready: "success",
  needs_revision: "warning",
  not_ready: "danger",
  unknown: "neutral",
};

export function ReadinessBadge({ readiness }: { readiness: Readiness }) {
  return <StatusBadge label={READINESS_LABEL[readiness]} tone={READINESS_TONE[readiness]} />;
}
