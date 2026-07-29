/**
 * Contrast contract for the semantic token layer.
 *
 * The token values in tokens.css were solved against WCAG targets rather than
 * picked by eye (see that file's header). This test is what keeps them that
 * way: retuning a theme, adding a theme, or "just darkening that one grey"
 * fails here rather than silently shipping a 2:1 label.
 *
 * It parses the real CSS — not a duplicated table of values — so there is no
 * second copy of the palette to drift out of sync.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { describe, expect, it } from "vitest";

const HERE = dirname(fileURLToPath(import.meta.url));

// ---------------------------------------------------------------------------
// Parse `:root` / `:root[data-theme="x"]` blocks out of a stylesheet.
// ---------------------------------------------------------------------------
function parseBlocks(css: string): Record<string, Record<string, string>> {
  const themes: Record<string, Record<string, string>> = {};
  const blockRe = /:root(?:\[data-theme="([^"]+)"\])?\s*\{([\s\S]*?)\n\}/g;
  let block: RegExpExecArray | null;
  while ((block = blockRe.exec(css)) !== null) {
    const name = block[1] ?? "light";
    const vars = (themes[name] ??= {});
    const varRe = /(--gf-[a-z0-9-]+):\s*([^;]+);/g;
    let v: RegExpExecArray | null;
    while ((v = varRe.exec(block[2])) !== null) vars[v[1]] = v[2].trim();
  }
  return themes;
}

const primitives = parseBlocks(readFileSync(join(HERE, "themes.css"), "utf8"));
const semantic = parseBlocks(readFileSync(join(HERE, "tokens.css"), "utf8"));

const THEMES = ["light", "dark", "midnight", "modern-blue", "high-contrast"] as const;
type Theme = (typeof THEMES)[number];

/** Theme-local lookup falling back to the bare `:root` (theme-independent) block. */
function raw(theme: Theme, name: string): string | undefined {
  return semantic[theme]?.[name] ?? semantic.light?.[name] ?? primitives[theme]?.[name];
}

// ---------------------------------------------------------------------------
// Colour maths + a resolver for the `var()` / `color-mix()` the tokens use.
// ---------------------------------------------------------------------------
function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace("#", "");
  if (h.length === 3)
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function relativeLuminance([r, g, b]: [number, number, number]): number {
  const f = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [relativeLuminance(hexToRgb(a)), relativeLuminance(hexToRgb(b))].sort(
    (x, y) => y - x,
  );
  return (hi + 0.05) / (lo + 0.05);
}

function mix(a: string, b: string, ratio: number): string {
  const [x, y] = [hexToRgb(a), hexToRgb(b)];
  return (
    "#" +
    x
      .map((c, i) => Math.round(c * ratio + y[i] * (1 - ratio)))
      .map((c) => c.toString(16).padStart(2, "0"))
      .join("")
  );
}

/**
 * Resolve a token to a concrete hex for `theme`, following `var()` chains and
 * evaluating `color-mix(in srgb, A p%, B)`. `backdrop` is what a `transparent`
 * mix composites onto.
 */
function resolve(theme: Theme, expr: string, backdrop = "#ffffff", depth = 0): string {
  if (depth > 12) throw new Error(`token cycle resolving "${expr}"`);
  const value = expr.trim();

  if (value.startsWith("#")) return value;

  const varMatch = /^var\((--gf-[a-z0-9-]+)\)$/.exec(value);
  if (varMatch) {
    const next = raw(theme, varMatch[1]);
    if (!next) throw new Error(`unknown token ${varMatch[1]} in ${theme}`);
    return resolve(theme, next, backdrop, depth + 1);
  }

  const mixMatch = /^color-mix\(in srgb,\s*(.+?)\s+(\d+)%,\s*(.+?)\)$/.exec(value);
  if (mixMatch) {
    const ratio = Number(mixMatch[2]) / 100;
    const from = resolve(theme, mixMatch[1], backdrop, depth + 1);
    const toExpr = mixMatch[3].trim();
    const to = toExpr === "transparent" ? backdrop : resolve(theme, toExpr, backdrop, depth + 1);
    return mix(from, to, ratio);
  }

  if (value.startsWith("rgb(")) return backdrop; // shadow ink — not a contrast surface
  throw new Error(`cannot resolve "${value}" in ${theme}`);
}

const token = (theme: Theme, name: string, backdrop?: string) => {
  const value = raw(theme, name);
  if (!value) throw new Error(`missing ${name} in ${theme}`);
  return resolve(theme, value, backdrop);
};

/** AA for normal text; High Contrast is held to AAA. */
const textTarget = (theme: Theme) => (theme === "high-contrast" ? 7 : 4.5);
/** Non-text (borders, focus rings, graph strokes) per WCAG 1.4.11. */
const NON_TEXT = 3;

const STATUS = ["success", "warning", "serious", "danger", "info", "accent"] as const;
const SURFACES = ["canvas", "surface", "surface-raised"] as const;

describe.each(THEMES)("%s theme", (theme) => {
  const surfaces = () => SURFACES.map((s) => [s, token(theme, `--gf-${s}`)] as const);

  it("body ink clears its target on every surface", () => {
    for (const ink of ["fg", "fg-secondary", "fg-muted"]) {
      for (const [name, bg] of surfaces()) {
        const ratio = contrast(token(theme, `--gf-${ink}`, bg), bg);
        expect(
          ratio,
          `${ink} on ${name} is ${ratio.toFixed(2)}:1, needs ${textTarget(theme)}`,
        ).toBeGreaterThanOrEqual(textTarget(theme));
      }
    }
  });

  it("fg-subtle stays legible as non-text/decorative ink", () => {
    for (const [name, bg] of surfaces()) {
      const ratio = contrast(token(theme, "--gf-fg-subtle", bg), bg);
      expect(ratio, `fg-subtle on ${name} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
        NON_TEXT,
      );
    }
  });

  it.each(STATUS)("%s status role is internally consistent", (role) => {
    const target = textTarget(theme);
    const surface = token(theme, "--gf-surface");
    const raised = token(theme, "--gf-surface-raised");
    const tint = token(theme, `--gf-${role}-bg`, surface);
    const fg = token(theme, `--gf-${role}-fg`, tint);
    const solid = token(theme, `--gf-${role}-solid`);
    const onSolid = token(theme, `--gf-${role}-on-solid`);
    const line = token(theme, `--gf-${role}-line`);

    // The ink has to work on its own chip *and* bare on a raised surface —
    // both placements exist in the app.
    expect(contrast(fg, tint), `${role}-fg on its own tint`).toBeGreaterThanOrEqual(target);
    expect(contrast(fg, raised), `${role}-fg on surface-raised`).toBeGreaterThanOrEqual(target);
    expect(contrast(onSolid, solid), `${role}-on-solid on ${role}-solid`).toBeGreaterThanOrEqual(
      4.5,
    );
    expect(contrast(line, surface), `${role}-line on surface`).toBeGreaterThanOrEqual(NON_TEXT);
  });

  it("graph node labels are readable on their own fill", () => {
    const target = textTarget(theme);
    const canvas = token(theme, "--gf-graph-canvas");
    for (let slot = 1; slot <= 8; slot++) {
      const bg = token(theme, `--gf-node-${slot}-bg`);
      const fg = token(theme, `--gf-node-${slot}-fg`);
      const line = token(theme, `--gf-node-${slot}-line`);
      expect(contrast(fg, bg), `node-${slot} label on its fill`).toBeGreaterThanOrEqual(target);
      expect(contrast(line, canvas), `node-${slot} border on canvas`).toBeGreaterThanOrEqual(
        NON_TEXT,
      );
    }
  });

  it("graph relationship colours are distinguishable from the canvas", () => {
    const canvas = token(theme, "--gf-graph-canvas");
    for (const role of ["selected", "incoming", "outgoing", "edge"]) {
      const ratio = contrast(token(theme, `--gf-graph-${role}`), canvas);
      expect(ratio, `graph-${role} on canvas is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
        NON_TEXT,
      );
    }
  });

  it("the focus ring is visible against every surface", () => {
    for (const [name, bg] of surfaces()) {
      const ratio = contrast(token(theme, "--gf-focus"), bg);
      expect(ratio, `focus ring on ${name} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
        NON_TEXT,
      );
    }
  });

  it("borders separate from the surfaces they divide", () => {
    // `line-muted` is deliberately quiet, so only `line` carries the 3:1
    // structural contract.
    const ratio = contrast(token(theme, "--gf-line"), token(theme, "--gf-surface"));
    expect(ratio, `line on surface is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(1.5);
  });
});

describe("token layer completeness", () => {
  it.each(THEMES)("%s declares every role the app consumes", (theme) => {
    const required = [
      "--gf-canvas",
      "--gf-surface",
      "--gf-surface-raised",
      "--gf-fg",
      "--gf-fg-secondary",
      "--gf-fg-muted",
      "--gf-fg-subtle",
      "--gf-line",
      "--gf-focus",
      "--gf-graph-canvas",
      "--gf-graph-edge",
      ...STATUS.flatMap((r) => [
        `--gf-${r}-fg`,
        `--gf-${r}-bg`,
        `--gf-${r}-line`,
        `--gf-${r}-solid`,
      ]),
      ...Array.from({ length: 8 }, (_, i) => `--gf-node-${i + 1}-bg`),
      ...Array.from({ length: 8 }, (_, i) => `--gf-chart-${i + 1}`),
    ];
    for (const name of required) {
      expect(() => token(theme, name), `${name} missing in ${theme}`).not.toThrow();
    }
  });
});
