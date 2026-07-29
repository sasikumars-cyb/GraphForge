/**
 * Architectural guard: components consume semantic tokens, never primitives.
 *
 * The primitive ramps in themes.css (`slate-800`, `emerald-400`, ...) are the
 * raw palette. They carry no meaning, so the same step ends up serving as a
 * border in one file, a background in the next and body text in a third — and
 * then cannot be retuned for any of them. That collision is what produced
 * every contrast failure in the original audit (74 co-occurring pairs and 132
 * inherited-surface pairs below AA across the five themes).
 *
 * Components now reference roles (`bg-surface`, `text-fg-muted`,
 * `border-line`, `text-danger-fg`, `bg-cat-3-bg`). This test keeps it that
 * way: a new `text-slate-500` fails here, with the token to use instead.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");

/** The palette families themes.css defines. */
const FAMILIES = [
  "slate",
  "brand",
  "emerald",
  "amber",
  "rose",
  "sky",
  "blue",
  "indigo",
  "violet",
  "orange",
  "red",
  "yellow",
  "teal",
  "pink",
].join("|");

/** `bg-slate-800`, `text-emerald-400/70`, `hover:ring-brand-500`, ... */
const RAW_UTILITY = new RegExp(
  String.raw`\b(?:[a-z-]+:)*(?:bg|text|border|ring|divide|from|via|to|fill|stroke|shadow|outline|placeholder|accent|caret|decoration)-(?:${FAMILIES})-\d{2,3}(?:/\d+)?\b`,
  "g",
);

/**
 * Files exempt from the rule, each for a stated reason — not a
 * "we'll get to it" list.
 */
const ALLOWED = new Set([
  // The theme registry's swatches must be literal: they preview what a theme
  // looks like, so they cannot resolve against the *active* theme.
  "theme/themes.ts",
]);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (/\.tsx?$/.test(path)) out.push(path);
  }
  return out;
}

describe("design token architecture", () => {
  it("no component references a raw palette utility", () => {
    const offenders: string[] = [];

    for (const path of walk(SRC)) {
      const rel = relative(SRC, path);
      if (ALLOWED.has(rel) || rel.endsWith(".test.ts") || rel.endsWith(".test.tsx")) continue;

      for (const [index, line] of readFileSync(path, "utf8").split("\n").entries()) {
        // Prose in a comment may legitimately name an old class.
        const code = line.replace(/\/\/.*$/, "").replace(/^\s*\*.*$/, "");
        for (const hit of code.matchAll(RAW_UTILITY)) {
          offenders.push(`${rel}:${index + 1}  ${hit[0]}`);
        }
      }
    }

    expect(
      offenders,
      `Raw palette utilities found. Use a semantic token instead:\n` +
        `  surfaces  bg-canvas / bg-surface / bg-surface-raised / bg-surface-hover\n` +
        `  ink       text-fg / text-fg-secondary / text-fg-muted / text-fg-subtle\n` +
        `  lines     border-line / border-line-muted / border-line-strong\n` +
        `  status    {success,warning,serious,danger,info,accent}-{fg,bg,line,solid,on-solid}\n` +
        `  identity  cat-1..8-{bg,fg,line}   (categorical, assigned not cycled)\n` +
        `  graph     graph-{canvas,edge,selected,...}, node-1..8-{bg,line,fg}\n\n` +
        offenders.join("\n"),
    ).toEqual([]);
  });
});
