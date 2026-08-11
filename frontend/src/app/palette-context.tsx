import { createContext, useContext } from "react";

interface PaletteContextValue {
  /** Opens the single, global CommandPalette instance mounted in
   * AppLayout — see that component's docstring for why open state lives
   * there rather than inside CommandPalette itself. Exists so a page's
   * own content (Mission Control's header search affordance) can trigger
   * the exact same palette Topbar's "⌘K" button does, instead of a
   * second, competing search surface. */
  openPalette: () => void;
}

// Default is a safe no-op, not `null` + a throwing hook — a page that
// renders this context's consumer in isolation (every page test in this
// codebase mounts its page directly, not through AppLayout) would
// otherwise need to wrap every render in a provider just to avoid a
// crash, for a button whose own open/close behavior isn't what that
// page's tests are about.
export const PaletteContext = createContext<PaletteContextValue>({
  openPalette: () => {},
});

export function usePalette(): PaletteContextValue {
  return useContext(PaletteContext);
}
