# GraphForge — Presentation Deck

A 42-slide Reveal.js presentation for the GraphForge hackathon talk.
Pure HTML/CSS/JS — **no build step, no npm, no bundler.**

## Run it

Just open the file:

```bash
open presentation/index.html        # macOS
xdg-open presentation/index.html    # Linux
start presentation/index.html       # Windows
```

Or serve it locally (recommended for Chrome's stricter local-file
policies with some fetch-based CDN assets):

```bash
cd presentation
python3 -m http.server 8080
# then open http://localhost:8080
```

## Controls

| Key | Action |
|---|---|
| `→` / `Space` | Next slide / fragment |
| `←` | Previous slide / fragment |
| `S` | Speaker notes window (what to say, per slide) |
| `F` | Fullscreen |
| `Esc` | Slide overview grid |
| `?` | Full keyboard shortcut list |

Every slide has speaker notes — press **S** to open the presenter view in
a second window (works great on a second monitor/projector setup).

## What's in here

```
presentation/
  index.html          all 42 slides, single file
  css/style.css        GraphForge dark/glassmorphic theme
  js/custom.js          Reveal.js + Mermaid initialization
  assets/logo.svg       GraphForge mark (title slide, favicon)
  diagrams/
    pipeline.svg         Evidence -> Hypothesis -> Validation -> Confidence -> Knowledge
    aws-architecture.svg  ALB -> ECS Fargate x2 -> RDS + Neo4j -> Bedrock
  images/               (reserved — empty, no raster assets used)
```

## Content structure (8 acts, 42 slides)

1. **Open** — title, agenda
2. **The Problem** — problem statement, current challenges, why existing
   tools fail, vision
3. **Introducing GraphForge** — product pillars
4. **Architecture** — pipeline overview, Engineering Memory, Knowledge
   Engine, Validators, Confidence Engine, Materializer, Cross-Repository
   Reasoning
5. **AI & Agents** — Service Layer, Frontier AI, Agent framework,
   Repository Understanding, Dependency Query, Impact Analysis
6. **Quality** — the 24-repository validation framework
7. **Cloud** — AWS architecture
8. **Demo, Why We Win, Honesty, Close** — demo flow, innovation,
   engineering excellence, scalability, security, testing, performance,
   known limitations, roadmap, business value, summary, Q&A

## Source of truth

Every claim on every slide traces to `docs/handbook/` or
`docs/presentation/` — nothing here was invented for visual effect. Known
limitations (slide 38) and the "honest caveat" callouts throughout are
deliberate, not omissions — see `docs/presentation/12_REALITY_CHECK_PRESENTATION.md`
for the full, unabridged version each presenter should read before
speaking.

## External dependencies (all via CDN, zero local install)

- [Reveal.js](https://revealjs.com/) 5.1.0 — presentation framework
  (core, notes/highlight/markdown plugins)
- [Mermaid](https://mermaid.js.org/) 10.x — the flowchart/sequence
  diagrams (Knowledge Engine pipeline, Agent execution flow, Materializer
  sequence)
- [Font Awesome](https://fontawesome.com/) 6.5 — icons
- Google Fonts — Inter (body), Space Grotesk (display), JetBrains Mono
  (code/labels)

Two architecture diagrams (`diagrams/pipeline.svg`,
`diagrams/aws-architecture.svg`) are hand-built static SVGs, not
generated — they render identically everywhere, including offline once
the page is cached, with no JS diagram engine required for those two
slides specifically.

## Editing

- **Add a slide**: copy any `<section data-background-color="#0B1020">…
  </section>` block in `index.html`, keep the numbered HTML comment
  headers in sync (`<!-- N. TITLE -->`) — they're for human navigation
  only, Reveal.js doesn't read them.
- **Change the palette**: edit the CSS custom properties at the top of
  `css/style.css` (`--primary`, `--neo4j`, `--aws`, `--ai`, `--success`,
  `--warning`, `--danger`) — every card/badge/chip/callout derives from
  these.
- **Add a Mermaid diagram**: wrap it in `<div class="mermaid-wrap"><div
  class="mermaid">…</div></div>` — `js/custom.js` re-renders any
  unprocessed `.mermaid` block on every slide change, so no manual
  `mermaid.init()` call is needed per-slide.
- **Speaker notes**: every `<section>` ends with `<aside
  class="notes">…</aside>` — keep the four-part shape (Say / Key points /
  Transition / Expect) so all 5 presenters can rehearse from a
  consistent format.

## Known constraints

- Requires internet access on presentation day (CDN-hosted Reveal.js/
  Mermaid/Font Awesome/fonts) — if presenting somewhere with unreliable
  Wi-Fi, test the venue's network beforehand or download the CDN assets
  locally and repoint the `<link>`/`<script>` tags.
- Mermaid diagrams render client-side on first view of each slide; on a
  very underpowered machine there may be a brief flash before a diagram
  settles — rehearse the deck once end-to-end on the actual presenting
  machine beforehand.
