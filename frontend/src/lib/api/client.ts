/**
 * Bare API client stub.
 *
 * Deliberately has no endpoint methods yet — it exists only to prove the
 * frontend can resolve the backend's base URL from the environment. Real
 * endpoint calls (and a data-fetching library, if one becomes warranted)
 * are added once there's an actual API surface to call.
 */

export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";
