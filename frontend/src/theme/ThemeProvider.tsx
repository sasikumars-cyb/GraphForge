import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { DEFAULT_THEME_ID, THEMES, getTheme, isValidThemeId } from "./themes";
import { ThemeContext } from "./theme-context";

export const THEME_STORAGE_KEY = "graphforge.theme";

function readStoredThemeId(): string {
  const stored = localStorage.getItem(THEME_STORAGE_KEY);
  return isValidThemeId(stored) ? stored : DEFAULT_THEME_ID;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [themeId, setThemeIdState] = useState<string>(() => readStoredThemeId());

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", themeId);
    localStorage.setItem(THEME_STORAGE_KEY, themeId);
  }, [themeId]);

  const setThemeId = useCallback((id: string) => {
    setThemeIdState(isValidThemeId(id) ? id : DEFAULT_THEME_ID);
  }, []);

  const value = useMemo(
    () => ({ themeId, theme: getTheme(themeId), themes: THEMES, setThemeId }),
    [themeId, setThemeId],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
