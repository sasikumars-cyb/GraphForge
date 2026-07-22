import { Link } from "react-router-dom";
import { Card } from "../components/Card";
import { StatusBadge } from "../components/StatusBadge";
import { useDashboardData } from "../hooks/useDashboardData";
import { repositoryHealthPresentation } from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import { FolderGit2 } from "lucide-react";

export function RepositoriesPage() {
  const { repositories, isLoading, error } = useDashboardData();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Repositories</h2>
        <p className="mt-1 text-sm text-slate-400">
          Repositories tracked and indexed by ChangeGuard.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {!isLoading && repositories.length === 0 && (
        <p className="text-sm text-slate-500">No repositories tracked yet.</p>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {repositories.map((repo) => {
          const { label, tone } = repositoryHealthPresentation(repo.health);
          return (
            <Link key={repo.id} to={`/repositories/${repo.id}`}>
              <Card className="flex flex-col gap-4 transition hover:border-slate-600">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <FolderGit2 className="h-4 w-4 text-sky-400" aria-hidden="true" />
                    <p className="font-medium text-slate-100">{repo.fullName}</p>
                  </div>
                  <StatusBadge label={label} tone={tone} />
                </div>

                <dl className="grid grid-cols-2 gap-3 text-sm">
                  <div>
                    <dt className="text-xs text-slate-500">Open PRs</dt>
                    <dd className="font-medium text-slate-200">{repo.openPullRequests}</dd>
                  </div>
                </dl>

                <p className="text-xs text-slate-500">
                  Tracked since {formatRelativeTime(repo.createdAt)}
                </p>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
