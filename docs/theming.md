# Theming

GraphForge ships five themes — Light (default), Dark, Midnight, Modern Blue,
and High Contrast — switchable from **Settings → Workspace → Appearance**,
persisted in `localStorage`, and applied consistently across the whole app
(navigation, dashboard, workflow pages, graph/blueprint diagrams, forms,
modals, tables, buttons, and status colors) without any component needing to
know a theme system exists.

## How it works

The frontend is styled entirely with Tailwind v4 utility classes
(`bg-slate-900`, `text-emerald-400`, `border-brand-500`, ...). Tailwind v4
generates each of those from a `--color-*` custom property registered in an
`@theme` block. [`src/styles/themes.css`](../frontend/src/styles/themes.css)
exploits that: instead of literal values, its `@theme inline` block points
every one of those Tailwind color variables at a plain custom property
(`--gf-slate-900`, `--gf-emerald-400`, ...):

```css
@theme inline {
  --color-slate-900: var(--gf-slate-900);
  --color-emerald-400: var(--gf-emerald-400);
  /* ...every family/shade actually used in src/**/*.tsx */
}
```

Each theme is then just a block that redeclares those `--gf-*` properties
under a `[data-theme="..."]` attribute selector on `<html>`:

```css
:root[data-theme="midnight"] {
  --gf-slate-950: #02040f;
  --gf-slate-900: #0a0e24;
  /* ... */
}
```

Because every existing component already renders via `bg-slate-900` etc.,
redeclaring the tokens re-themes the entire application retroactively —
**no component file changes when a theme is added or retuned.** This is
also why the color families in `themes.css` only cover the specific shades
(`slate` 50–950, `brand` 50–950, plus the handful of `sky`/`emerald`/`amber`/
`rose`/... shades actually referenced) — anything unused would be dead
weight.

`:root` (no `data-theme` attribute) holds the **Light** theme's values,
since Light is the app default and must be correct even for the instant
before JavaScript runs.

## Pieces

| File | Role |
|---|---|
| [`frontend/src/styles/themes.css`](../frontend/src/styles/themes.css) | The token layer: one block per theme, plus the `@theme inline` bridge into Tailwind. |
| [`frontend/src/theme/themes.ts`](../frontend/src/theme/themes.ts) | The theme **registry** — id, label, description, `mode` (`"light"`/`"dark"`, read by library integrations like `@xyflow/react`'s `colorMode`), and a 4-color swatch for the picker UI. |
| [`frontend/src/theme/ThemeProvider.tsx`](../frontend/src/theme/ThemeProvider.tsx) | Reads/writes `localStorage["graphforge.theme"]`, stamps `data-theme` on `<html>`. Mounted outermost in `App.tsx` so the (unauthenticated) login page is themed too. |
| [`frontend/src/theme/theme-context.ts`](../frontend/src/theme/theme-context.ts) | `useTheme()` hook — `{ themeId, theme, themes, setThemeId }`. |
| [`frontend/src/theme/ThemeSelector.tsx`](../frontend/src/theme/ThemeSelector.tsx) | The swatch-grid picker, embedded in `WorkspaceSection.tsx`'s "Appearance" card. Renders one button per registry entry — nothing to update when a theme is added. |
| `frontend/index.html` | A tiny inline script that stamps `data-theme` from `localStorage` before React mounts, so there's no flash of the wrong theme on load. **Its hardcoded theme-id list must stay in sync with `themes.ts`** (see below). |

## Adding a new theme

Two steps, both required:

1. **Add a CSS block** to `frontend/src/styles/themes.css`:
   `:root[data-theme="your-id"] { --gf-slate-950: ...; --gf-brand-500: ...; ... }`.
   Copy an existing block (Dark is the simplest starting point) and retune
   values — you don't need to touch the `@theme inline` bridge or any other
   theme's block.
2. **Add a registry entry** to `THEMES` in `frontend/src/theme/themes.ts`
   with the matching `id`, a `label`/`description`, `mode: "light" | "dark"`,
   and a `swatch` (four representative colors for the picker preview).
3. **Add the same `id`** to the `validThemes` array in the inline script in
   `frontend/index.html` (this list can't `import` from `themes.ts` since it
   runs before any JS bundle loads) — otherwise a saved preference for the
   new theme won't survive the FOUC-avoidance check on reload.

No component file needs to change. If the new theme reuses an existing
mode's accent-color values (most dark-background themes can), copy those
lines verbatim from an existing same-mode block rather than re-deriving
them.

### Choosing values / contrast

- If the new theme is dark-background, its `--gf-{family}-{shade}` values
  can generally reuse the same values as the `dark` block — those were
  chosen to read well on a near-black background.
- If it's light-background (like `light` or `high-contrast`), a shade like
  `emerald-300` or `sky-400` — designed as light/pastel text-on-dark — needs
  a **darker** substitute of the same hue to stay legible on a light
  surface. The existing `light`/`high-contrast` blocks show one way to pick
  these (favor the family's own darker stock shades, e.g. `emerald-700`
  standing in for what "300" reads as in that theme).
- Verify contrast before shipping. There's no build-time linting for this;
  the fastest manual check is opening the app, then in the console:

  ```js
  function luminance(hex) {
    const c = hex.replace('#', '');
    const [r, g, b] = [0, 2, 4].map((i) => parseInt(c.slice(i, i + 2), 16) / 255);
    const lin = (v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  }
  function contrast(a, b) {
    const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (l1 + 0.05) / (l2 + 0.05);
  }
  document.documentElement.setAttribute('data-theme', 'your-id');
  const cs = getComputedStyle(document.documentElement);
  contrast(cs.getPropertyValue('--gf-slate-100'), cs.getPropertyValue('--gf-slate-950'));
  ```

  Aim for ≥ 4.5:1 (WCAG AA) on primary text/background pairs; the shipped
  themes mostly clear 10:1+.

## Known scope limits

A few call sites draw their own colors as intentional, self-contained
"badges" rather than reading from the theme — each already pairs a
background/border/text combination chosen for contrast against *itself*,
independent of the page background, so they render correctly in every
theme without needing to change:

- Per-node-type colors in `frontend/src/components/graph/graphLabels.ts`
  (`Controller`, `Service`, `KafkaTopic`, ...).
- Per-node-type and per-severity colors in
  `frontend/src/components/blueprint/BlueprintRenderer.tsx`
  (`NODE_STYLES`, `RISK_SEVERITY_COLORS`) — apart from the `default`/
  `component` entries' background, which *is* theme-reactive.
- Selection-highlight glow colors in `DependencyGraph.tsx`/
  `BlueprintRenderer.tsx` (amber/emerald/sky overlay on a clicked node).

Everything else — canvas background, edges, minimap, and every ordinary
component — is theme-reactive through the token layer described above.
