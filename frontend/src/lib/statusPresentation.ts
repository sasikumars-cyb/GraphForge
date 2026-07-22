import type { PullRequestStatus, RepositoryHealth, ReportStatus } from "../types/domain";
import type { StatusTone } from "../components/StatusBadge";

/**
 * Maps each domain status enum to display label + StatusBadge tone.
 * Centralized so "open" always looks the same everywhere it appears.
 */

export function pullRequestStatusPresentation(status: PullRequestStatus): {
  label: string;
  tone: StatusTone;
} {
  switch (status) {
    case "analyzing":
      return { label: "Analyzing", tone: "info" };
    case "open":
      return { label: "Open", tone: "neutral" };
    case "blocked":
      return { label: "Blocked", tone: "danger" };
    case "merged":
      return { label: "Merged", tone: "success" };
  }
}

export function repositoryHealthPresentation(health: RepositoryHealth): {
  label: string;
  tone: StatusTone;
} {
  switch (health) {
    case "healthy":
      return { label: "Healthy", tone: "success" };
    case "attention":
      return { label: "Needs attention", tone: "warning" };
    case "critical":
      return { label: "Critical", tone: "danger" };
  }
}

export function reportStatusPresentation(status: ReportStatus): {
  label: string;
  tone: StatusTone;
} {
  switch (status) {
    case "ready":
      return { label: "Ready", tone: "success" };
    case "generating":
      return { label: "Generating", tone: "info" };
    case "failed":
      return { label: "Failed", tone: "danger" };
  }
}
