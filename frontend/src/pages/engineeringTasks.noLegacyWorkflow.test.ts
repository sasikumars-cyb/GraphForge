import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Phase 7.2 — structural proof (source-text scan, mirroring the backend's
 * own AST-boundary-test convention) that none of the new Engineering Task
 * pages/API module import anything from the legacy Workflow/Run system.
 * The two systems remain separate for this phase, per instruction.
 */

const FILES = [
  "EngineeringTaskListPage.tsx",
  "NewEngineeringTaskPage.tsx",
  "EngineeringTaskDetailPage.tsx",
];

describe("Engineering Task pages have no legacy Workflow dependency", () => {
  it.each(FILES)("%s does not import lib/api/workflows or lib/api/agentRuns", (filename) => {
    const path = join(process.cwd(), "src/pages", filename);
    const source = readFileSync(path, "utf-8");
    // Match only actual import statements (quoted module specifiers), not
    // prose mentions in comments/docstrings — EngineeringTaskDetailPage.tsx's
    // own docstring names lib/api/workflows.ts precisely to say it doesn't
    // import it.
    expect(source).not.toMatch(/from ["'].*lib\/api\/workflows["']/);
    expect(source).not.toMatch(/from ["'].*lib\/api\/agentRuns["']/);
  });

  it("lib/api/engineeringTasks.ts does not import lib/api/workflows or lib/api/agentRuns", () => {
    const path = join(process.cwd(), "src/lib/api/engineeringTasks.ts");
    const source = readFileSync(path, "utf-8");
    // Match only actual import statements (quoted module specifiers), not
    // prose mentions in comments/docstrings — EngineeringTaskDetailPage.tsx's
    // own docstring names lib/api/workflows.ts precisely to say it doesn't
    // import it.
    expect(source).not.toMatch(/from ["'].*lib\/api\/workflows["']/);
    expect(source).not.toMatch(/from ["'].*lib\/api\/agentRuns["']/);
  });
});
