import { useEffect, useState } from "react";
import { Activity, CheckCircle2, AlertTriangle, Clock, Loader2 } from "lucide-react";
import { Card } from "../Card";
import { StatusBadge } from "../StatusBadge";
import { useAuth } from "../../app/auth-context";
import { ApiError } from "../../lib/api/client";
import {
  listProviders,
  upsertProvider,
  validateProvider,
  getAISettings,
  setDefaultProvider,
  type ProviderInfo,
  type ValidationResponse,
  type AIWorkspaceSettings as AISettingsDTO,
} from "../../lib/api/ai-providers";

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function statusTone(status: string): "success" | "warning" | "danger" | "neutral" {
  switch (status) {
    case "ready":
      return "success";
    case "rate_limited":
      return "warning";
    case "auth_failed":
    case "offline":
      return "danger";
    default:
      return "neutral";
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case "ready":
      return "Healthy";
    case "rate_limited":
      return "Rate limited";
    case "auth_failed":
      return "Auth failed";
    case "offline":
      return "Offline";
    case "unknown":
      return "Not validated";
    default:
      return status;
  }
}

function formatLatency(ms: number | null): string {
  if (ms === null) return "--";
  return `${ms}ms`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "Just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  return d.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Primary Provider Card
// ---------------------------------------------------------------------------

function PrimaryProviderCard({
  providers,
  settings,
}: {
  providers: ProviderInfo[];
  settings: AISettingsDTO | null;
}) {
  const defaultKey = settings?.default_provider ?? null;
  const primary = (defaultKey && providers.find((p) => p.key === defaultKey)) || null;

  if (!primary) {
    return (
      <Card title="Primary Provider" description="No default provider selected">
        <p className="text-sm text-slate-400">
          {providers.some((p) => p.configured)
            ? 'No provider is set as default yet. Choose one below and click "Set as Default".'
            : "Configure an AI provider below to power GraphForge agents."}
        </p>
      </Card>
    );
  }

  return (
    <Card title="Primary Provider" description="Active provider powering all agent workflows">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-emerald-500/10 ring-1 ring-inset ring-emerald-500/30">
            <Activity className="h-6 w-6 text-emerald-400" />
          </div>
          <div>
            <p className="text-base font-semibold text-slate-100">{primary.label}</p>
            <p className="text-sm text-slate-400">
              {primary.model ?? primary.default_model}
            </p>
          </div>
        </div>
        <StatusBadge label={statusLabel(primary.status)} tone={statusTone(primary.status)} />
      </div>

      <div className="mt-4 grid grid-cols-3 gap-4 border-t border-slate-800 pt-4">
        <div>
          <p className="text-xs text-slate-500">Status</p>
          <p className="mt-0.5 text-sm font-medium text-slate-200">{statusLabel(primary.status)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Latency</p>
          <p className="mt-0.5 text-sm font-medium text-slate-200">{formatLatency(primary.latency_ms)}</p>
        </div>
        <div>
          <p className="text-xs text-slate-500">Last Success</p>
          <p className="mt-0.5 text-sm font-medium text-slate-200">{formatTime(primary.last_success_at)}</p>
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Provider Configuration Panel (expandable)
// ---------------------------------------------------------------------------

function ProviderConfigPanel({
  provider,
  isDefault,
  onSaved,
}: {
  provider: ProviderInfo;
  isDefault: boolean;
  onSaved: () => void;
}) {
  const { token } = useAuth();
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(provider.model ?? provider.default_model);
  const [region, setRegion] = useState(provider.base_url ?? "");
  const [makeDefault, setMakeDefault] = useState(isDefault);
  const [isSaving, setIsSaving] = useState(false);
  const [isValidating, setIsValidating] = useState(false);
  const [validation, setValidation] = useState<ValidationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    if (!token) return;
    setIsSaving(true);
    setError(null);
    try {
      const body: Record<string, unknown> = { model };
      if (apiKey) body.api_key = apiKey;
      // Bedrock stores region in the base_url column.
      if (region) body.base_url = region;
      await upsertProvider(token, provider.key, body);
      if (makeDefault && !isDefault) {
        await setDefaultProvider(token, provider.key, model);
      }
      onSaved();
    } catch (err) {
      setError(messageFrom(err, "Failed to save."));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleValidate() {
    if (!token) return;
    setIsValidating(true);
    setValidation(null);
    setError(null);
    try {
      const result = await validateProvider(token, provider.key, model);
      setValidation(result);
    } catch (err) {
      setError(messageFrom(err, "Validation failed."));
    } finally {
      setIsValidating(false);
    }
  }

  return (
    <div className="mt-3 space-y-3 border-t border-slate-800 pt-3">
      {provider.requires_api_key && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-slate-400">
            API Key{" "}
            {provider.api_key_configured && (
              <span className="text-emerald-400">(configured)</span>
            )}
          </span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={
              provider.api_key_configured ? "Leave blank to keep current" : "Enter API key"
            }
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
          />
        </label>
      )}

      {!provider.requires_api_key && provider.key === "bedrock" && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-slate-400">AWS Region</span>
          <input
            type="text"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            placeholder="us-east-1"
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
          />
          <span className="text-xs text-slate-500">
            Credentials from AWS SDK credential chain (env, CLI, IAM role).
          </span>
        </label>
      )}

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-slate-400">Model</span>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
        >
          {provider.models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label}
              {m.context_window ? ` (${Math.round(m.context_window / 1000)}K)` : ""}
            </option>
          ))}
        </select>
      </label>

      {error && (
        <p role="alert" className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          {error}
        </p>
      )}
      {validation && (
        <div
          className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm ${
            validation.ok
              ? "bg-emerald-500/10 text-emerald-300"
              : "bg-rose-500/10 text-rose-300"
          }`}
        >
          {validation.ok ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <AlertTriangle className="h-4 w-4" />
          )}
          <span>
            {validation.message}
            {validation.ok && ` (${validation.latency_ms}ms)`}
          </span>
        </div>
      )}

      <label className="flex items-center gap-2 text-sm text-slate-400">
        <input
          type="checkbox"
          checked={makeDefault}
          disabled={isDefault}
          onChange={(e) => setMakeDefault(e.target.checked)}
          className="h-3.5 w-3.5 rounded border-slate-700 bg-slate-950 accent-sky-500"
        />
        {isDefault ? "This is the default provider" : "Set as default provider"}
      </label>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={isSaving}
          className="rounded-md bg-sky-500 px-3 py-1.5 text-xs font-semibold text-black hover:bg-sky-400 disabled:cursor-not-allowed disabled:bg-sky-500/50"
        >
          {isSaving ? "Saving..." : "Save"}
        </button>
        <button
          type="button"
          onClick={() => void handleValidate()}
          disabled={isValidating}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 hover:bg-slate-800 disabled:opacity-50"
        >
          {isValidating ? "Testing..." : "Test Connection"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Provider list (Configured / Available)
// ---------------------------------------------------------------------------

function ProviderList({
  providers,
  defaultProviderKey,
  onSaved,
}: {
  providers: ProviderInfo[];
  defaultProviderKey: string | null;
  onSaved: () => void;
}) {
  const { token } = useAuth();
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [settingDefaultKey, setSettingDefaultKey] = useState<string | null>(null);
  const [defaultError, setDefaultError] = useState<string | null>(null);

  const configured = providers.filter((p) => p.implemented && p.configured);
  const available = providers.filter((p) => p.implemented && !p.configured);

  async function handleSetDefault(p: ProviderInfo) {
    if (!token) return;
    setSettingDefaultKey(p.key);
    setDefaultError(null);
    try {
      await setDefaultProvider(token, p.key, p.model ?? p.default_model);
      onSaved();
    } catch (err) {
      setDefaultError(messageFrom(err, `Failed to set ${p.label} as default.`));
    } finally {
      setSettingDefaultKey(null);
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {defaultError && (
        <p role="alert" className="rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-300">
          {defaultError}
        </p>
      )}
      {configured.length > 0 && (
        <Card title="Configured Providers" description="Providers with active configuration">
          <div className="divide-y divide-slate-800/60">
            {configured.map((p) => {
              const isDefault = p.key === defaultProviderKey;
              return (
                <div key={p.key} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium text-slate-200">
                        {p.label}
                        {isDefault && (
                          <span className="ml-2 rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-300 ring-1 ring-inset ring-sky-500/30">
                            Default
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-slate-500">
                        {p.model ?? p.default_model}
                        {p.latency_ms != null && ` \u00B7 ${p.latency_ms}ms`}
                        {p.last_success_at && ` \u00B7 Last used ${formatTime(p.last_success_at)}`}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <StatusBadge
                        label={statusLabel(p.status)}
                        tone={statusTone(p.status)}
                      />
                      {!isDefault && (
                        <button
                          type="button"
                          onClick={() => void handleSetDefault(p)}
                          disabled={settingDefaultKey === p.key}
                          className="rounded-md border border-sky-700 px-2.5 py-1 text-xs font-medium text-sky-300 hover:bg-sky-500/10 disabled:opacity-50"
                        >
                          {settingDefaultKey === p.key ? "Setting..." : "Set as Default"}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => setExpandedKey(expandedKey === p.key ? null : p.key)}
                        className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-medium text-slate-300 hover:bg-slate-800"
                      >
                        {expandedKey === p.key ? "Close" : "Configure"}
                      </button>
                    </div>
                  </div>
                  {expandedKey === p.key && (
                    <ProviderConfigPanel provider={p} isDefault={isDefault} onSaved={onSaved} />
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {available.length > 0 && (
        <Card title="Available Providers" description="Providers ready to configure">
          <div className="divide-y divide-slate-800/60">
            {available.map((p) => (
              <div key={p.key} className="py-3 first:pt-0 last:pb-0">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium text-slate-200">{p.label}</p>
                    <p className="text-xs text-slate-500">{p.notes || "Ready to configure"}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setExpandedKey(expandedKey === p.key ? null : p.key)}
                    className="rounded-md border border-slate-700 px-2.5 py-1 text-xs font-medium text-slate-300 hover:bg-slate-800"
                  >
                    {expandedKey === p.key ? "Close" : "Configure"}
                  </button>
                </div>
                {expandedKey === p.key && (
                  <ProviderConfigPanel
                    provider={p}
                    isDefault={p.key === defaultProviderKey}
                    onSaved={onSaved}
                  />
                )}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main AI Providers Section (Settings tab — configuration, not agent execution;
// see src/pages/WorkspacePage.tsx for the "AI Workspace" agent-execution catalog)
// ---------------------------------------------------------------------------

export function AIWorkspaceSection() {
  const { token } = useAuth();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [settings, setSettings] = useState<AISettingsDTO | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadProviders() {
    if (!token) return;
    setIsLoading(true);
    try {
      const [providerList, aiSettings] = await Promise.all([
        listProviders(token),
        getAISettings(token),
      ]);
      setProviders(providerList);
      setSettings(aiSettings);
      setError(null);
    } catch (err) {
      setError(messageFrom(err, "Failed to load providers."));
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadProviders();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading AI workspace...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
        {error}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <PrimaryProviderCard providers={providers} settings={settings} />
      <ProviderList
        providers={providers}
        defaultProviderKey={settings?.default_provider ?? null}
        onSaved={() => void loadProviders()}
      />

      {/* Profiles and Fallback — forward-looking cards */}
      <Card title="AI Profiles" description="Named configurations for different workflow stages">
        <div className="flex items-center gap-3 text-sm text-slate-400">
          <Clock className="h-4 w-4 text-slate-500" />
          <span>
            Configure profiles under AI Providers to assign different models and parameters
            per workflow stage (Planning, Development, Review, Testing).
          </span>
        </div>
      </Card>

      <Card title="Fallback Policy" description="Automatic provider failover on recoverable errors">
        <div className="flex items-center gap-3 text-sm text-slate-400">
          <AlertTriangle className="h-4 w-4 text-slate-500" />
          <span>
            When enabled, recoverable failures (rate limits, timeouts) automatically retry
            on the next provider in the configured fallback order.
          </span>
        </div>
      </Card>
    </div>
  );
}
