import { Check } from "lucide-react";
import { useTheme } from "./theme-context";

/**
 * Theme picker — renders one swatch button per entry in the theme registry
 * (src/theme/themes.ts), so adding a theme there is all a new option needs.
 */
export function ThemeSelector() {
  const { themeId, themes, setThemeId } = useTheme();

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {themes.map((option) => {
        const isActive = option.id === themeId;
        return (
          <button
            key={option.id}
            type="button"
            onClick={() => setThemeId(option.id)}
            aria-pressed={isActive}
            className={`group relative flex flex-col gap-3 rounded-lg border p-3 text-left transition-colors ${
              isActive
                ? "border-accent-line ring-1 ring-accent-line"
                : "border-line hover:border-line-strong"
            }`}
          >
            <span
              className="flex h-14 w-full items-center gap-1.5 overflow-hidden rounded-md border border-line-muted p-2"
              style={{ background: option.swatch.background }}
              aria-hidden="true"
            >
              <span
                className="h-full w-1/3 rounded-sm"
                style={{ background: option.swatch.surface }}
              />
              <span className="flex h-full flex-1 flex-col justify-center gap-1 rounded-sm px-1.5">
                <span
                  className="h-1.5 w-3/4 rounded-full"
                  style={{ background: option.swatch.text }}
                />
                <span
                  className="h-1.5 w-1/2 rounded-full"
                  style={{ background: option.swatch.accent }}
                />
              </span>
            </span>

            <span className="flex items-center justify-between gap-2">
              <span>
                <span className="block text-sm font-medium text-fg">{option.label}</span>
                <span className="block text-xs text-fg-muted">{option.description}</span>
              </span>
              {isActive && (
                <Check className="h-4 w-4 shrink-0 text-accent-fg" aria-hidden="true" />
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
