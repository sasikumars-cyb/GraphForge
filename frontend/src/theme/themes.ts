/**
 * Theme registry — the single source of truth for "which themes exist".
 *
 * Adding a new theme is two steps, both required:
 *   1. Add a `:root[data-theme="your-id"] { ... }` block to
 *      src/styles/themes.css declaring every `--gf-*` token.
 *   2. Add an entry here with the matching `id`.
 * No component file needs to change — every component already renders via
 * the Tailwind color utilities the CSS layer re-themes. See docs/theming.md.
 */

export type ThemeMode = "light" | "dark";

export interface ThemeDefinition {
  id: string;
  label: string;
  description: string;
  /** Governs library integrations that have their own light/dark switch
   *  (e.g. @xyflow/react's `colorMode` prop) rather than reading our CSS. */
  mode: ThemeMode;
  /** Swatch preview shown in the theme selector — four representative
   *  tokens, not an exhaustive palette dump. */
  swatch: {
    background: string;
    surface: string;
    accent: string;
    text: string;
  };
}

export const THEMES: ThemeDefinition[] = [
  {
    id: "light",
    label: "Light",
    description: "Clean and bright — the default.",
    mode: "light",
    swatch: { background: "#f8fafc", surface: "#f1f5f9", accent: "#4f46e5", text: "#0f172a" },
  },
  {
    id: "dark",
    label: "Dark",
    description: "GraphForge's original dark UI.",
    mode: "dark",
    swatch: { background: "#020617", surface: "#0f172a", accent: "#6366f1", text: "#f1f5f9" },
  },
  {
    id: "midnight",
    label: "Midnight",
    description: "Deeper, cooler blacks with a blue-violet undertone.",
    mode: "dark",
    swatch: { background: "#02040f", surface: "#0a0e24", accent: "#6f74f0", text: "#eef1fb" },
  },
  {
    id: "modern-blue",
    label: "Modern Blue",
    description: "Steel-blue neutrals with a vivid indigo-blue accent.",
    mode: "dark",
    swatch: { background: "#040a14", surface: "#0d1626", accent: "#5b78f5", text: "#e6edf9" },
  },
  {
    id: "high-contrast",
    label: "High Contrast",
    description: "Pure black/white with saturated accents for maximum readability.",
    mode: "light",
    swatch: { background: "#ffffff", surface: "#f5f5f5", accent: "#372ec2", text: "#000000" },
  },
];

export const DEFAULT_THEME_ID = "light";

export function isValidThemeId(id: string | null): id is string {
  return id !== null && THEMES.some((theme) => theme.id === id);
}

export function getTheme(id: string): ThemeDefinition {
  return THEMES.find((theme) => theme.id === id) ?? THEMES.find((theme) => theme.id === DEFAULT_THEME_ID)!;
}
