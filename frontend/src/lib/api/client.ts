/**
 * Minimal typed fetch wrapper. Kept deliberately small (no TanStack Query,
 * no axios) — auth is the first real endpoint surface; a data-fetching
 * library is worth adding once there's more than a handful of calls.
 */

export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** Matches the backend's error shape: {"error": {"code": ..., "message": ...}} */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

interface ApiFetchOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  token?: string | null;
}

export async function apiFetch<T>(path: string, options: ApiFetchOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (!response.ok) {
    const parsed = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      parsed?.error?.code ?? "unknown_error",
      parsed?.error?.message ?? `Request to ${path} failed with status ${response.status}.`,
    );
  }

  // No endpoint used here returns an empty body, but guard against it anyway.
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}
