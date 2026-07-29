import { useEffect, useState } from "react";
import { Brain, CheckCircle2, Loader2, XCircle, AlertTriangle } from "lucide-react";
import { Card } from "./Card";
import { StatusBadge } from "./StatusBadge";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import {
  listProviders,
  upsertProvider,
  validateProvider,
  type ProviderInfo,
  type ValidationResponse,
} from "../lib/api/ai-providers";

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
      return "Ready";
    case "rate_limited":
      return "Rate limited";
    case "auth_failed":
      return "Auth failed";
    case "offline":
      return "Offline";
    case "unknown":
      return "Not tested";
    default:
      return status;
  }
}

interface ProviderConfigPanelProps {
  provider: ProviderInfo;
  onSaved: () => void;
}

function ProviderConfigPanel({ provider, onSaved }: ProviderConfigPanelProps) {
  const { token } = useAuth();
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(provider.model ?? provider.default_model);
  const [region, setRegion] = useState(provider.base_url ?? "");
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
      // Bedrock stores its region in the base_url column (it has no actual URL).
      if (region) body.base_url = region;
      await upsertProvider(token, provider.key, body);
      onSaved();
    } catch (err) {
      setError(messageFrom(err, "Failed to save provider configuration."));
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
      setError(messageFrom(err, "Validation request failed."));
    } finally {
      setIsValidating(false);
    }
  }

  return (
    <div className="mt-3 space-y-3 border-t border-line-muted pt-3">
      {provider.requires_api_key && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-muted">
            API Key {provider.api_key_configured && <span className="text-success-fg">(configured)</span>}
          </span>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={provider.api_key_configured ? "Leave blank to keep current" : "Enter API key"}
            className="rounded-md border border-line bg-canvas px-3 py-2 text-fg focus:border-info-line"
          />
        </label>
      )}

      {!provider.requires_api_key && provider.key === "bedrock" && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-fg-muted">AWS Region</span>
          <input
            type="text"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            placeholder="us-east-1"
            className="rounded-md border border-line bg-canvas px-3 py-2 text-fg focus:border-info-line"
          />
          <span className="text-xs text-fg-muted">
            Credentials are read from the AWS SDK credential chain (env vars, CLI profile, IAM role).
          </span>
        </label>
      )}

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-fg-muted">Model</span>
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="rounded-md border border-line bg-canvas px-3 py-2 text-fg focus:border-info-line"
        >
          {provider.models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} {m.context_window ? `(${Math.round(m.context_window / 1000)}K ctx)` : ""}
            </option>
          ))}
        </select>
      </label>

      {error && (
        <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger-fg">
          {error}
        </p>
      )}

      {validation && (
        <div
          className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm ${
            validation.ok ? "bg-success-bg text-success-fg" : "bg-danger-bg text-danger-fg"
          }`}
        >
          {validation.ok ? (
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          ) : (
            <XCircle className="h-4 w-4" aria-hidden="true" />
          )}
          <span>
            {validation.message}
            {validation.ok && ` (${validation.latency_ms}ms)`}
          </span>
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={isSaving}
          className="rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:cursor-not-allowed disabled:bg-info-bg"
        >
          {isSaving ? "Saving..." : "Save"}
        </button>
        <button
          type="button"
          onClick={() => void handleValidate()}
          disabled={isValidating}
          className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-fg-secondary hover:bg-surface-raised disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isValidating ? "Testing..." : "Test connection"}
        </button>
      </div>
    </div>
  );
}

export function AIProvidersCard() {
  const { token } = useAuth();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  async function loadProviders() {
    if (!token) return;
    setIsLoading(true);
    try {
      const data = await listProviders(token);
      setProviders(data);
    } catch (err) {
      setError(messageFrom(err, "Couldn't load AI providers."));
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadProviders();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  function toggleExpand(key: string) {
    setExpandedKey(expandedKey === key ? null : key);
  }

  // Only show implemented providers in the main list.
  const implemented = providers.filter((p) => p.implemented);

  return (
    <Card title="AI Providers" description="Configure which AI provider powers GraphForge agents">
      <div className="flex flex-col gap-3">
        {isLoading ? (
          <div className="flex items-center gap-2 py-4 text-sm text-fg-muted">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading providers...
          </div>
        ) : error ? (
          <p role="alert" className="rounded-md bg-danger-bg px-3 py-2 text-sm text-danger-fg">
            {error}
          </p>
        ) : (
          implemented.map((provider) => (
            <div
              key={provider.key}
              className="rounded-lg border border-line-muted px-4 py-3"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <Brain className="h-5 w-5 text-fg-secondary" aria-hidden="true" />
                  <div>
                    <p className="text-sm font-medium text-fg-secondary">{provider.label}</p>
                    <p className="text-xs text-fg-muted">
                      {provider.configured
                        ? `Model: ${provider.model ?? provider.default_model}`
                        : provider.notes || "Not configured"}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge label={statusLabel(provider.status)} tone={statusTone(provider.status)} />
                  <button
                    type="button"
                    onClick={() => toggleExpand(provider.key)}
                    className="rounded-md border border-line px-3 py-1.5 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
                  >
                    {expandedKey === provider.key ? "Close" : "Configure"}
                  </button>
                </div>
              </div>

              {expandedKey === provider.key && (
                <ProviderConfigPanel provider={provider} onSaved={() => void loadProviders()} />
              )}
            </div>
          ))
        )}

        {!isLoading && !error && implemented.length === 0 && (
          <div className="flex items-center gap-2 py-4 text-sm text-fg-muted">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            No AI providers available.
          </div>
        )}
      </div>
    </Card>
  );
}
