import { useState } from "react";
import { Card } from "../components/Card";
import { GitHubIntegrationCard } from "../components/GitHubIntegrationCard";

interface ToggleRowProps {
  label: string;
  description: string;
  defaultChecked?: boolean;
}

/** Local to Settings — not promoted to a shared component since nothing
 * else needs a labeled toggle switch yet. */
function ToggleRow({ label, description, defaultChecked = false }: ToggleRowProps) {
  const [checked, setChecked] = useState(defaultChecked);

  return (
    <label className="flex cursor-pointer items-start justify-between gap-4 py-3">
      <span>
        <span className="block text-sm font-medium text-slate-200">{label}</span>
        <span className="block text-xs text-slate-500">{description}</span>
      </span>
      <span className="relative inline-flex h-6 w-11 shrink-0 items-center">
        <input
          type="checkbox"
          checked={checked}
          onChange={(event) => setChecked(event.target.checked)}
          className="peer sr-only"
        />
        <span className="h-6 w-11 rounded-full bg-slate-700 transition-colors peer-checked:bg-sky-500" />
        <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white transition-transform peer-checked:translate-x-5" />
      </span>
    </label>
  );
}

export function SettingsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Settings</h2>
        <p className="mt-1 text-sm text-slate-400">
          Workspace preferences. GitHub is the only integration that's real — everything else on
          this page is still a placeholder.
        </p>
      </div>

      <Card title="Organization">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-400">Organization name</span>
            <input
              type="text"
              defaultValue="Acme Engineering"
              className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-400">Default branch</span>
            <input
              type="text"
              defaultValue="main"
              className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
            />
          </label>
        </div>
      </Card>

      <Card
        title="Notifications"
        description="How ChangeGuard should notify you about analyzed changes"
      >
        <div className="divide-y divide-slate-800">
          <ToggleRow
            label="Email me on critical risk"
            description="Sent as soon as a pull request is scored critical"
            defaultChecked
          />
          <ToggleRow
            label="Daily digest"
            description="A summary of the day's analyzed pull requests"
            defaultChecked
          />
          <ToggleRow
            label="Slack notifications"
            description="Post analysis results to a Slack channel"
          />
        </div>
      </Card>

      <GitHubIntegrationCard />
    </div>
  );
}
