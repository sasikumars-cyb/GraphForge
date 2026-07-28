import { useState } from "react";
import { Card } from "../Card";
import { ThemeSelector } from "../../theme/ThemeSelector";

function ToggleRow({
  label,
  description,
  defaultChecked = false,
}: {
  label: string;
  description: string;
  defaultChecked?: boolean;
}) {
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
          onChange={(e) => setChecked(e.target.checked)}
          className="peer sr-only"
        />
        <span className="h-6 w-11 rounded-full bg-slate-700 transition-colors peer-checked:bg-sky-500" />
        <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white transition-transform peer-checked:translate-x-5" />
      </span>
    </label>
  );
}

export function WorkspaceSection() {
  return (
    <div className="flex flex-col gap-5">
      <Card title="Organization" description="General workspace identity and defaults">
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
        description="How GraphForge alerts you about important events"
      >
        <div className="divide-y divide-slate-800">
          <ToggleRow
            label="Critical risk alerts"
            description="Notify immediately when a pull request is scored critical"
            defaultChecked
          />
          <ToggleRow
            label="Daily digest"
            description="Summary of the day's analyzed pull requests and workflow runs"
            defaultChecked
          />
          <ToggleRow
            label="Slack integration"
            description="Post analysis results and run completions to a Slack channel"
          />
          <ToggleRow
            label="Agent failure alerts"
            description="Notify when an AI agent run fails or requires intervention"
            defaultChecked
          />
        </div>
      </Card>

      <Card title="Appearance" description="Choose how GraphForge looks on this device">
        <ThemeSelector />
      </Card>

      <Card
        title="Preferences"
        description="Workflow defaults and display settings"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-400">Default workflow type</span>
            <select className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500">
              <option value="full">Full (Plan + Develop + Test + Review)</option>
              <option value="plan_only">Plan Only</option>
              <option value="review_only">Review Only</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-slate-400">Auto-approve threshold</span>
            <select className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500">
              <option value="never">Never (require manual approval)</option>
              <option value="low">Low risk changes only</option>
              <option value="medium">Medium risk and below</option>
            </select>
          </label>
        </div>
      </Card>
    </div>
  );
}
