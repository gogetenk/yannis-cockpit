"use client";
import { useEffect, useState } from "react";
import { fmtHeaderDate } from "@/lib/utils";

interface Props { today: string }

export function AppHeader({ today }: Props) {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const saved = (typeof window !== "undefined" && localStorage.getItem("cockpit-theme")) as "light" | "dark" | null;
    if (saved) setTheme(saved);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("cockpit-theme", theme); } catch {}
  }, [theme]);

  const dateLabel = fmtHeaderDate(new Date(today));

  return (
    <header className="app" role="banner">
      <time className="date" dateTime={today}>{dateLabel}</time>

      <button
        className="theme-toggle"
        type="button"
        aria-label="Basculer thème clair/sombre"
        title="Thème"
        onClick={() => setTheme(t => t === "light" ? "dark" : "light")}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          {theme === "light" ? (
            <>
              <circle cx="12" cy="12" r="4"/>
              <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>
            </>
          ) : (
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
          )}
        </svg>
      </button>

      <a className="chat" href="#" aria-label="Ouvrir Claude pour discuter">
        <span>Claude</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>
        </svg>
      </a>
    </header>
  );
}
