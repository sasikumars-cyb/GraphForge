import { API_BASE_URL, ApiError, UNAUTHORIZED_EVENT, apiFetch } from "./client";

export interface TestCaseUpload {
  id: string;
  filename: string;
  display_name: string;
  case_count: number;
  uploaded_by_user_id: string;
  created_at: string;
}

/** Shared across every user, like TestRail-synced cases — see
 * backend app.models.test_case_upload's docstring. */
export function listTestCaseUploads(token: string): Promise<TestCaseUpload[]> {
  return apiFetch<TestCaseUpload[]>("/test-cases/uploads", { token });
}

export function deleteTestCaseUpload(token: string, uploadId: string): Promise<void> {
  return apiFetch<void>(`/test-cases/uploads/${uploadId}`, { method: "DELETE", token });
}

/** Multipart upload — apiFetch always JSON-encodes its body, which can't
 * carry a File, so this makes its own fetch() call instead. Mirrors
 * apiFetch's error handling (ApiError shape, invalid_token event) so
 * callers get identical behavior either way. */
export async function uploadTestCaseFile(
  token: string,
  file: File,
  name: string,
): Promise<TestCaseUpload> {
  const form = new FormData();
  form.append("file", file);
  if (name.trim()) {
    form.append("name", name.trim());
  }

  const response = await fetch(`${API_BASE_URL}/test-cases/uploads`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });

  if (!response.ok) {
    const parsed = await response.json().catch(() => null);
    const code = parsed?.error?.code ?? "unknown_error";
    if (code === "invalid_token") {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    throw new ApiError(
      response.status,
      code,
      parsed?.error?.message ?? `Upload failed with status ${response.status}.`,
    );
  }

  return response.json() as Promise<TestCaseUpload>;
}
