/* GraphForge presentation — Reveal.js + Mermaid initialization.
 * Pure browser JS, no build step. Loaded after the Reveal.js core/plugin
 * CDN scripts and after Mermaid's CDN script in index.html.
 */

// ---------------------------------------------------------------------
// Mermaid — dark theme tuned to the GraphForge palette
// ---------------------------------------------------------------------
if (window.mermaid) {
  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    securityLevel: "loose",
    fontFamily: "Inter, -apple-system, Segoe UI, sans-serif",
    themeVariables: {
      background: "#0B1020",
      primaryColor: "#4F46E522",
      primaryBorderColor: "#4F46E5",
      primaryTextColor: "#F4F6FB",
      lineColor: "#818CF8",
      secondaryColor: "#00A86B22",
      secondaryBorderColor: "#00A86B",
      tertiaryColor: "#8B5CF622",
      tertiaryBorderColor: "#8B5CF6",
      noteBkgColor: "#10172B",
      noteTextColor: "#A6AFC3",
      noteBorderColor: "#4F46E5",
      actorBkg: "#10172B",
      actorBorder: "#4F46E5",
      actorTextColor: "#F4F6FB",
      signalColor: "#818CF8",
      signalTextColor: "#F4F6FB",
      labelBoxBkgColor: "#10172B",
      labelTextColor: "#F4F6FB",
      fontSize: "16px",
    },
    flowchart: { curve: "basis", htmlLabels: true, padding: 12 },
    sequence: { actorMargin: 60, boxMargin: 10, messageFontSize: 13 },
  });
}

// ---------------------------------------------------------------------
// Reveal.js
// ---------------------------------------------------------------------
Reveal.initialize({
  hash: true,
  slideNumber: "c/t",
  progress: true,
  controls: true,
  center: false,
  transition: "slide",
  transitionSpeed: "default",
  backgroundTransition: "fade",
  width: 1280,
  height: 720,
  margin: 0.06,
  minScale: 0.2,
  maxScale: 1.6,
  plugins: [RevealNotes, RevealHighlight, RevealMarkdown],
}).then(() => {
  // Render any Mermaid diagrams present on the currently-reached slide,
  // and re-render on every slide change — Mermaid needs the container to
  // be visible/sized, which isn't reliably true until Reveal has laid the
  // slide out.
  const renderMermaidIn = (root) => {
    if (!window.mermaid) return;
    const blocks = root.querySelectorAll(".mermaid:not([data-processed])");
    blocks.forEach((el) => (el.dataset.processed = "true"));
    if (blocks.length) {
      mermaid.run({ nodes: blocks });
    }
  };

  renderMermaidIn(document);

  Reveal.on("slidechanged", (event) => {
    renderMermaidIn(event.currentSlide);
  });

  Reveal.on("fragmentshown", (event) => {
    // A fragment can reveal a mermaid block that wasn't in the initial
    // slide render pass.
    renderMermaidIn(event.fragment.parentElement || document);
  });
});
