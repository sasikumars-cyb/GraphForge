import { createContext, useContext } from "react";
import type { ThemeDefinition } from "./themes";

export interface ThemeContextValue {
  themeId: string;
  theme: ThemeDefinition;
  themes: ThemeDefinition[];
  setThemeId: (id: string) => void;
}

export const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return context;
}
