import { API_BASE_URL, ApiError } from "./client";

/** Mirrors the backend's `ExportFormat` (app.api.v1.routers.api_intelligence). */
export type ApiIntelligenceExportFormat = "openapi" | "postman" | "markdown" | "html" | "json";

const EXTENSIONS: Record<ApiIntelligenceExportFormat, string> = {
  openapi: "yaml",
  postman: "json",
  markdown: "md",
  html: "html",
  json: "json",
};

/** GET /api-intelligence/runs/{run_id}/export/{format} — a pure re-render
 * of an already-completed run (see the router's own docstring: no LLM
 * call, no side effect). Returns raw text rather than going through
 * `apiFetch` — that helper always JSON-parses the body, which only one of
 * these five formats actually is. */
export async function fetchApiIntelligenceExport(
  token: string,
  runId: string,
  format: ApiIntelligenceExportFormat,
): Promise<string> {
  const response = await fetch(
    `${API_BASE_URL}/api-intelligence/runs/${encodeURIComponent(runId)}/export/${format}`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  if (!response.ok) {
    const parsed = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      parsed?.error?.code ?? "unknown_error",
      parsed?.error?.message ?? `Export request failed with status ${response.status}.`,
    );
  }
  return response.text();
}

/** Triggers a browser download of already-fetched export content — kept
 * separate from the fetch above so the HTML dashboard can be fetched once
 * and both rendered inline (iframe `srcDoc`) and offered as a download
 * without a second request. */
export function downloadApiIntelligenceExport(
  content: string,
  runId: string,
  format: ApiIntelligenceExportFormat,
): void {
  const blob = new Blob([content], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `api-intelligence-${runId}.${EXTENSIONS[format]}`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
