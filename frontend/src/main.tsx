import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./app/App";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element with id 'root' was not found in index.html");
}

// No <StrictMode> — its dev-only double-invoked effects break @xyflow/react's
// internal node-measurement lifecycle (nodes get stuck at `visibility:
// hidden`, permanently unpositioned, at position (0,0) on top of each
// other — the "sparse/broken diagram" bug traced live via DevTools:
// every node in every Visual Blueprint diagram shared identical
// coordinates and `visibility: hidden`). Already on the latest
// @xyflow/react (12.11.2); no upgrade fixes this, so removing StrictMode
// is the actual fix rather than the fitView/ResizeObserver workarounds
// tried first.
createRoot(rootElement).render(<App />);
