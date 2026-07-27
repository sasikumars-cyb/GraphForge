import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError, UNAUTHORIZED_EVENT } from "./client";

function mockFetchResponse(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
}

describe("apiFetch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    // vi.stubGlobal("fetch", ...) below is not undone by restoreAllMocks —
    // without this, the stubbed fetch leaks into every other test file run
    // in the same worker, breaking any test that relies on the real
    // fetch/other API mocks being intact.
    vi.unstubAllGlobals();
  });

  it("dispatches UNAUTHORIZED_EVENT when the backend reports invalid_token", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchResponse(401, { error: { code: "invalid_token", message: "Invalid authentication token." } }),
    );
    const listener = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, listener);

    await expect(apiFetch("/whatever", { token: "dead-token" })).rejects.toThrow(ApiError);

    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener(UNAUTHORIZED_EVENT, listener);
  });

  it("does NOT dispatch UNAUTHORIZED_EVENT for an unrelated 401 (e.g. GitHub not connected)", async () => {
    /* Regression guard: a 401 raised for a business-logic reason against an
     * otherwise-valid session (see app.core.exceptions.UnauthorizedError's
     * docstring on the backend) must never log a valid user out just
     * because they hit an endpoint like "GitHub is not connected yet". */
    vi.stubGlobal(
      "fetch",
      mockFetchResponse(401, {
        error: { code: "unauthorized", message: "GitHub is not connected for this user." },
      }),
    );
    const listener = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, listener);

    await expect(apiFetch("/github/repositories", { token: "still-valid-token" })).rejects.toThrow(
      ApiError,
    );

    expect(listener).not.toHaveBeenCalled();
    window.removeEventListener(UNAUTHORIZED_EVENT, listener);
  });

  it("does not dispatch UNAUTHORIZED_EVENT on success", async () => {
    vi.stubGlobal("fetch", mockFetchResponse(200, { ok: true }));
    const listener = vi.fn();
    window.addEventListener(UNAUTHORIZED_EVENT, listener);

    await apiFetch("/whatever", { token: "fine-token" });

    expect(listener).not.toHaveBeenCalled();
    window.removeEventListener(UNAUTHORIZED_EVENT, listener);
  });
});
