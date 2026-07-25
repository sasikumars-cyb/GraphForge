import type { RepositoryHealth } from "../types/domain";
import type { StatusTone } from "../components/StatusBadge";

/**
 * Maps each domain status enum to display label + StatusBadge tone.
 * Centralized so "open" always looks the same everywhere it appears.
 */

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
