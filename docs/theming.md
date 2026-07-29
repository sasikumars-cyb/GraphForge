# Theming

GraphForge ships five themes — Light (default), Dark, Midnight, Modern Blue,
and High Contrast — switchable from **Settings → Workspace → Appearance**,
persisted in `localStorage`, and applied consistently across the whole app
(navigation, dashboard, workflow pages, graph/blueprint diagrams, forms,
modals, tables, buttons, and status colors) without any component needing to
know a theme system exists.

## How it works

Theming is **two layers**, and the distinction matters:

### Layer 1 — primitives (`src/styles/themes.css`)

The raw palette. Tailwind v4 generates every colour utility from a
`--color-*` custom property, so an `@theme inline` block points each one at a
plain custom property instead of a literal:

```css
@theme inline {
  --color-slate-900: var(--gf-slate-900);
  --color-emerald-400: var(--gf-emerald-400);
}
```

Each theme redeclares those `--gf-*` values under a `[data-theme="..."]`
selector on `<html>`. Every ramp is written **app-background-first**:
`slate-950` is always the canvas and `slate-50` always the strongest ink, in
light and dark themes alike. That inversion is what lets one mapping serve
all five themes.

### Layer 2 — semantic roles (`src/styles/tokens.css`)

**Components consume this layer, never layer 1.** A primitive carries no
meaning: `slate-700` was simultaneously the default border, a background and
a text colour depending on the call site, so it could not be tuned for any of
them without breaking the other two. That single collision produced every
contrast failure in the original audit — 74 co-occurring foreground/background
pairs and 132 inherited-surface pairs below WCAG AA across the five themes,
including approval-banner body text at **1.02:1** in Light.

So `tokens.css` names the roles:

| Group | Roles |
|---|---|
| Surfaces | `canvas`, `canvas-subtle`, `surface`, `surface-raised`, `surface-sunken`, `surface-hover`, `surface-active`, `overlay` |
| Ink | `fg`, `fg-secondary`, `fg-muted`, `fg-subtle`, `fg-on-solid` |
| Lines | `line`, `line-muted`, `line-strong` |
| Status | `success`, `warning`, `serious`, `danger`, `info`, `accent`, `neutral` — each with `-fg`, `-bg`, `-line`, `-solid`, `-on-solid` |
| Identity | `cat-1..8` with `-bg`, `-fg`, `-line` — a *categorical* palette, assigned in fixed order and **never cycled** |
| Graph | `graph-canvas`, `graph-edge`, `graph-edge-label`, `graph-cluster`, `graph-selected`, `graph-incoming`, `graph-outgoing`, `node-1..8-{bg,line,fg}` |
| Charts | `chart-1..8`, `chart-grid`, `chart-axis` |
| Diff | `diff-{added,removed,modified}-{bg,fg,line}` |
| System | `focus`, `selection`, `code-bg`, `terminal-bg` |

which components use as ordinary utilities:

```jsx
<div className="rounded-xl border border-line-muted bg-surface shadow-sm">
  <p className="text-fg-muted">…</p>
  <span className="bg-danger-bg text-danger-fg ring-danger-line/40">Failed</span>
</div>
```

Most roles are plain aliases onto the primitives, so **one `:root` mapping
serves every theme**. A role only gets an explicit per-theme value where the
primitive would fail its contrast target; each such block says why.

Shape, elevation and motion are systematised the same way in `index.css`:
a four-step radius ladder tied to component size, a five-step two-layer
shadow scale with **per-theme shadow ink** (a black drop shadow is invisible
on a near-black canvas, which is why the dark themes used to read as flat),
and a single focus treatment applied by a zero-specificity `:where()` base
rule so every focusable element has a visible indicator by default.

## Pieces

| File | Role |
|---|---|
| [`frontend/src/styles/themes.css`](../frontend/src/styles/themes.css) | **Layer 1 — primitives.** One palette block per theme, plus the `@theme inline` bridge into Tailwind. |
| [`frontend/src/styles/tokens.css`](../frontend/src/styles/tokens.css) | **Layer 2 — semantic roles.** What components actually consume. Mostly aliases onto layer 1; explicit per-theme values only where a primitive would fail its contrast target. |
| [`frontend/src/styles/tokens.test.ts`](../frontend/src/styles/tokens.test.ts) | Parses both stylesheets and asserts the contrast contract for all five themes. |
| [`frontend/src/styles/no-raw-palette.test.ts`](../frontend/src/styles/no-raw-palette.test.ts) | Fails if a component reaches past the semantic layer to a primitive utility. |
| [`frontend/src/index.css`](../frontend/src/index.css) | Radius / elevation / motion scales, the global focus treatment, and the `@xyflow/react` chrome bridge. |
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
mode's accent-colour values (most dark-background themes can), copy those
lines verbatim from an existing same-mode block rather than re-deriving them.

Then check whether any **semantic** role needs an explicit value for the new
theme: add a block to `tokens.css` for anything where the aliased primitive
would miss its contrast target. `npx vitest run src/styles/` tells you
exactly which roles those are.

### Choosing values / contrast

**This is enforced, not advisory.** `src/styles/tokens.test.ts` parses the
real CSS and asserts the contract for every theme, so a new theme fails the
suite rather than shipping a 2:1 label:

| Contract | Target |
|---|---|
| `fg`, `fg-secondary`, `fg-muted` on `canvas` / `surface` / `surface-raised` | 4.5:1 (7:1 in High Contrast) |
| `fg-subtle` on any surface | 3:1 — decorative/icon ink only |
| `{status}-fg` on its own tint **and** bare on `surface-raised` | 4.5:1 (7:1 HC) |
| `{status}-on-solid` on `{status}-solid` | 4.5:1 |
| `{status}-line` on `surface` | 3:1 |
| `node-N-fg` on `node-N-bg` | 4.5:1 (7:1 HC) |
| `node-N-line`, `graph-{edge,selected,incoming,outgoing}` on the graph canvas | 3:1 |
| `focus` on every surface | 3:1 |

A second guard, `src/styles/no-raw-palette.test.ts`, fails if any component
reaches past the semantic layer to a primitive utility — that is what keeps
the two layers from collapsing back into one over time.

The categorical `node-*` / `chart-*` hues are additionally validated as a set
for colour-vision-deficiency separation (OKLab ΔE ≥ 8 between adjacent slots)
and ≥ 3:1 against the canvas, in both light and dark steps.

To pick values for a new theme, solve for the target rather than guessing:
adjust a hue's lightness until it clears the ratio against the surface it
actually renders on, then run `npx vitest run src/styles/` to confirm.

## Graph and diagram colour

Previously the per-node-type colours in `graphLabels.ts` and
`BlueprintRenderer.tsx` were hardcoded dark-only hexes, documented here as
"self-contained badges [that] render correctly in every theme". They did not:
a node drew a fixed `#0c4a6e` background while its label ink was
`var(--gf-slate-200)`, which *is* theme-reactive — so in the light themes the
label inverted to near-black over that fixed dark navy, about **1.3:1**. The
selection highlight was a fixed `#facc15` gold sitting at 1.9:1 on the light
canvas.

Both now read from the token layer:

- **Node type → categorical slot.** `graphLabels.ts` maps each label to one of
  `node-1..8`; `BlueprintRenderer`'s `NODE_STYLES` does the same for diagram
  node types. Background, border **and label ink all come from the same
  slot**, so the label is guaranteed readable on the fill it sits on.
- **Severity is a status scale, not a categorical one.** `RISK_SEVERITY_COLORS`
  reads `danger`/`serious`/`warning`/`success`, so "critical" is the same red
  as every other error surface in the app.
- **Relationship highlights** (selected / incoming / outgoing) use
  `graph-selected` / `graph-incoming` / `graph-outgoing` — deliberately
  distinct from the categorical slots so a highlight can never be mistaken for
  a node type.
- **Library chrome.** `@xyflow/react`'s `colorMode` prop only knows "light" or
  "dark", so Midnight and Modern Blue got plain Dark's controls and High
  Contrast got Light's. `index.css` points xyflow's own `--xy-*` custom
  properties at our tokens, so the controls, minimap, edges, handles and dot
  grid follow the active theme.

Because these are `var()` references in inline styles, a theme switch
retints every graph with no React work at all.

## Adding a new colour to a component

Reach for a **role**, not a hue. If nothing fits, the missing thing is
usually a role rather than a colour — add it to `tokens.css` for all five
themes (and to the contract in `tokens.test.ts`) rather than dropping a
primitive into the component.
