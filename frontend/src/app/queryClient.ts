import { QueryClient } from "@tanstack/react-query";

// KAN-37 — the shared data-fetching layer `lib/api/client.ts` deferred
// ("worth adding once there's more than a handful of calls") until
// enough AI-driven pages existed to justify it. Defaults are
// deliberately conservative rather than TanStack Query's own
// defaults: `refetchOnWindowFocus: false` because several pages poll
// on their own cadence already (RunDetailPage, WorkflowPage) and a
// focus-triggered refetch on top of that would just be a redundant
// request, not a correctness fix; `retry: 1` because `apiFetch`
// already throws a typed `ApiError` for a real 4xx (a token
// problem, a 404) that retrying blindly would never fix — one retry
// covers a genuine transient network blip without masking a real
// failure behind unnecessary delay.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
});
