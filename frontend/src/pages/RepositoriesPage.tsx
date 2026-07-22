import { Card } from "../components/Card";
import { StatusBadge } from "../components/StatusBadge";
import { mockRepositories } from "../lib/mock/repositories";
import { repositoryHealthPresentation } from "../lib/statusPresentation";
import { formatRelativeTime } from "../lib/formatDate";
import { FolderGit2 } from "lucide-react";

export function RepositoriesPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Repositories</h2>
        <p className="mt-1 text-sm text-slate-400">
          Repositories ChangeGuard would monitor once connected. Shown here with sample data.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {mockRepositories.map((repo) => {
          const { label, tone } = repositoryHealthPresentation(repo.health);
          return (
            <Card key={repo.id} className="flex flex-col gap-4">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2">
                  <FolderGit2 className="h-4 w-4 text-sky-400" aria-hidden="true" />
                  <p className="font-medium text-slate-100">{repo.name}</p>
                </div>
                <StatusBadge label={label} tone={tone} />
              </div>

              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <dt className="text-xs text-slate-500">Services</dt>
                  <dd className="font-medium text-slate-200">{repo.services}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-500">Open PRs</dt>
                  <dd className="font-medium text-slate-200">{repo.openPullRequests}</dd>
                </div>
              </dl>

              <p className="text-xs text-slate-500">
                Last analyzed {formatRelativeTime(repo.lastAnalyzed)}
              </p>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
