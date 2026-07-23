# GraphForge — frontend

React + TypeScript SPA, built with Vite. See the [root README](../README.md) and [`docs/architecture/overview.md`](../docs/architecture/overview.md) for the full project context.

## Commands

```bash
npm install
npm run dev            # dev server, http://localhost:5173
npm run build           # production build to dist/
npm run lint            # oxlint
npm run format:check    # prettier --check
npm run test            # vitest
```

## Structure

```
src/
  app/         App shell + router config
  pages/       Route-level components
  features/    Reserved for feature-sliced modules (empty until there's a feature)
  components/  Shared UI components
  lib/         Cross-cutting utilities (API base URL, etc.)
  hooks/       Shared React hooks
  types/       Shared TypeScript types
```
