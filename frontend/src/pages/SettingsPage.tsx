import { useEffect, useState } from "react";
import { useAuth } from "../app/auth-context";
import { Card } from "../components/Card";
import { GitHubIntegrationCard } from "../components/GitHubIntegrationCard";
import { getExternalContextSettings, saveExternalContextSettings } from "../lib/api/externalContext";

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
  const { token } = useAuth();
  const [providers, setProviders] = useState<Record<string, Record<string, string | undefined>>>({
    jira: {},
    testrail: {},
    google_drive: {},
  });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const response = await getExternalContextSettings(token ?? undefined);
        const incoming = response.providers ?? {};
        setProviders({
          jira: incoming.jira ?? {},
          testrail: incoming.testrail ?? {},
          google_drive: incoming.google_drive ?? {},
        });
      } catch {
        // Keep defaults if the endpoint is unavailable.
      }
    })();
  }, [token]);

  const updateProviderValue = (provider: string, key: string, value: string) => {
    setProviders((current) => ({
      ...current,
      [provider]: {
        ...current[provider],
        [key]: value,
      },
    }));
  };

  const handleSave = async () => {
    setSaved(false);
    await saveExternalContextSettings({ providers: { jira: providers.jira, testrail: providers.testrail, google_drive: providers.google_drive } }, token ?? undefined);
    setSaved(true);
  };

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

      <Card title="External Context Providers" description="Configure sources the Testing agent can use when building a plan.">
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-4">
              <h3 className="text-sm font-semibold text-slate-100">Jira</h3>
              <div className="mt-3 space-y-3 text-sm">
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Base URL</span>
                  <input value={providers.jira.base_url ?? ""} onChange={(event) => updateProviderValue("jira", "base_url", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Account email</span>
                  <input value={providers.jira.email ?? ""} onChange={(event) => updateProviderValue("jira", "email", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">API token</span>
                  <input type="password" value={providers.jira.api_token ?? ""} onChange={(event) => updateProviderValue("jira", "api_token", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Project key</span>
                  <input value={providers.jira.project_key ?? ""} onChange={(event) => updateProviderValue("jira", "project_key", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
              </div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-4">
              <h3 className="text-sm font-semibold text-slate-100">TestRail</h3>
              <div className="mt-3 space-y-3 text-sm">
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Base URL</span>
                  <input value={providers.testrail.base_url ?? ""} onChange={(event) => updateProviderValue("testrail", "base_url", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">API key</span>
                  <input type="password" value={providers.testrail.api_key ?? ""} onChange={(event) => updateProviderValue("testrail", "api_key", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Project ID</span>
                  <input value={providers.testrail.project_id ?? ""} onChange={(event) => updateProviderValue("testrail", "project_id", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Section ID</span>
                  <input value={providers.testrail.section_id ?? ""} onChange={(event) => updateProviderValue("testrail", "section_id", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
              </div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-4">
              <h3 className="text-sm font-semibold text-slate-100">Google Drive</h3>
              <div className="mt-3 space-y-3 text-sm">
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Service account JSON</span>
                  <textarea value={providers.google_drive.service_account_json ?? ""} onChange={(event) => updateProviderValue("google_drive", "service_account_json", event.target.value)} rows={5} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Client credentials</span>
                  <textarea value={providers.google_drive.client_credentials ?? ""} onChange={(event) => updateProviderValue("google_drive", "client_credentials", event.target.value)} rows={3} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-slate-400">Folder/File IDs</span>
                  <input value={providers.google_drive.file_ids ?? ""} onChange={(event) => updateProviderValue("google_drive", "file_ids", event.target.value)} className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500" />
                </label>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button type="button" onClick={handleSave} className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white">Save providers</button>
            {saved && <span className="text-sm text-emerald-400">Saved</span>}
          </div>
        </div>
      </Card>

      <Card
        title="Notifications"
        description="How GraphForge should notify you about analyzed changes"
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
