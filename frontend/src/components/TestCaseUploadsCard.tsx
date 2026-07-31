import { useEffect, useRef, useState } from "react";
import { FileSpreadsheet, Plus, Trash2 } from "lucide-react";
import { useAuth } from "../app/auth-context";
import { ApiError } from "../lib/api/client";
import {
  deleteTestCaseUpload,
  listTestCaseUploads,
  uploadTestCaseFile,
  type TestCaseUpload,
} from "../lib/api/testCaseUploads";

function messageFrom(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

/** Upload a CSV/Excel file of test cases directly — the file-based
 * counterpart to a TestRail sync, for teams whose cases live in a
 * spreadsheet. Rendered unconditionally in Settings -> Integrations
 * (not nested inside the TestRail connection row): unlike syncing a
 * TestRail project, this needs no TestRail account at all. Same
 * collapsed-header/"+ Add" shell as the other cards on this page. */
export function TestCaseUploadsCard() {
  const { token } = useAuth();
  const [uploads, setUploads] = useState<TestCaseUpload[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function load() {
    if (!token) return;
    setLoading(true);
    try {
      setUploads(await listTestCaseUploads(token));
      setError(null);
    } catch (err) {
      setError(messageFrom(err, "Couldn't load test case uploads."));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !file) return;
    setIsUploading(true);
    setError(null);
    setNotice(null);
    try {
      const created = await uploadTestCaseFile(token, file, name);
      setNotice(`Uploaded '${created.display_name}' — ${created.case_count} test case(s).`);
      setName("");
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setShowAdd(false);
      await load();
    } catch (err) {
      setError(messageFrom(err, "Couldn't upload this file."));
    } finally {
      setIsUploading(false);
    }
  }

  async function handleDelete(upload: TestCaseUpload) {
    if (!token) return;
    setDeletingId(upload.id);
    try {
      await deleteTestCaseUpload(token, upload.id);
      await load();
    } catch (err) {
      setError(messageFrom(err, "Couldn't delete this upload."));
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="rounded-lg border border-line-muted bg-surface px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-8 w-8 items-center justify-center rounded-md bg-surface-raised text-fg-secondary">
            <FileSpreadsheet className="h-4 w-4" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-medium text-fg-secondary">Test Cases (File Upload)</p>
            <p className="text-xs text-fg-muted">
              Upload a CSV or Excel file of test cases — no TestRail account needed
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setShowAdd(!showAdd)}
          className="flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-xs font-medium text-fg-secondary hover:bg-surface-raised"
        >
          <Plus className="h-3 w-3" />
          Add Connection
        </button>
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded-md bg-danger-bg px-3 py-2 text-sm text-danger-fg">
          {error}
        </p>
      )}
      {notice && !error && (
        <p className="mt-3 rounded-md bg-success-bg px-3 py-2 text-sm text-success-fg">{notice}</p>
      )}

      {!loading && uploads && uploads.length > 0 && (
        <ul className="mt-3 divide-y divide-line-muted border-t border-line-muted pt-1">
          {uploads.map((upload) => (
            <li key={upload.id} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-sm text-fg-secondary">{upload.display_name}</p>
                <p className="text-xs text-fg-muted">
                  {upload.case_count} case(s) · {upload.filename} · {formatTime(upload.created_at)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => void handleDelete(upload)}
                disabled={deletingId === upload.id}
                className="shrink-0 text-fg-muted hover:text-danger-fg disabled:opacity-40"
                title="Delete upload"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {showAdd && (
        <form
          onSubmit={(e) => void handleUpload(e)}
          className="mt-3 flex flex-col gap-3 border-t border-line-muted pt-3"
        >
          <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
            Upload a test case file
          </p>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-fg-muted">Name (optional — defaults to the filename)</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Checkout regression suite"
              className="rounded-md border border-line-strong bg-canvas px-3 py-1.5 text-xs text-fg-secondary"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-fg-muted">File (.csv or .xlsx)</span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,.xlsx,.xlsm"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-xs text-fg-secondary file:mr-3 file:rounded-md file:border file:border-line file:bg-surface-raised file:px-2.5 file:py-1 file:text-xs file:font-medium file:text-fg-secondary"
            />
          </label>
          <button
            type="submit"
            disabled={isUploading || !file}
            className="self-start rounded-md bg-info-solid px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 disabled:cursor-not-allowed disabled:bg-info-bg"
          >
            {isUploading ? "Uploading…" : "Upload"}
          </button>
        </form>
      )}
    </div>
  );
}
