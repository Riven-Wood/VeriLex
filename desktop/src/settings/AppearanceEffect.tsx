import { useEffect } from "react";
import { useUiPreferences } from "./preferences";
export function AppearanceEffect() {
  const preferences = useUiPreferences();
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    const apply = () => {
      document.documentElement.dataset.theme = preferences.theme === "system" ? (query?.matches ? "dark" : "light") : preferences.theme;
      document.documentElement.style.setProperty("--vl-font-scale", preferences.fontSize === "large" ? "1.15" : preferences.fontSize === "small" ? "0.92" : "1");
    };
    apply(); query?.addEventListener?.("change", apply);
    return () => query?.removeEventListener?.("change", apply);
  }, [preferences.theme, preferences.fontSize]);
  return null;
}
