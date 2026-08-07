/** Architecture Page V2's navigation state — one source of truth driving
 * both what's rendered and the breadcrumb trail (`ArchitectureBreadcrumbs`
 * reads this same union, never a separately-tracked path). Deliberately
 * not URL-encoded (no router params) — the existing page didn't have deep
 * links either (`?repository=`), and adding real deep-linkable routes for
 * four levels is a bigger, separate change than this redesign's own
 * scope. */
export type ArchitectureView =
  | { level: "landing" }
  | { level: "domain"; domain: string }
  | { level: "repository"; repositoryId: string; repositoryName: string; domain: string | null }
  | {
      level: "neighborhood";
      repositoryId: string;
      repositoryName: string;
      domain: string | null;
      nodeId: string;
      nodeLabel: string;
    };
