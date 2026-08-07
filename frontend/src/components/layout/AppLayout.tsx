import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { CommandPalette } from "./CommandPalette";

/**
 * The app shell: a fixed sidebar on desktop (md+), collapsing to an
 * off-canvas drawer on mobile, opened via the Topbar's menu button.
 */
export function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // Lifted out of CommandPalette itself so Topbar's visible "⌘K" button and
  // the palette's own keyboard shortcut can both drive the same state —
  // otherwise the shortcut is undiscoverable to anyone who doesn't already
  // know it exists.
  const [paletteOpen, setPaletteOpen] = useState(false);

  return (
    <div className="flex min-h-screen bg-canvas text-fg">
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar onMenuClick={() => setSidebarOpen(true)} onOpenPalette={() => setPaletteOpen(true)} />
        <main className="flex-1 overflow-y-auto p-4 md:p-8">
          <div className="mx-auto max-w-6xl">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Global, not per-page — Cmd/Ctrl+K works from anywhere in the
          authenticated app, matching where AppLayout itself is mounted
          (inside RequireAuth in router.tsx). */}
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}
