"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * Minimal top bar for the in-app pages (launcher, run detail, etc).
 *
 * Hidden on the landing route (`/`) because the landing has its own
 * full-width sticky nav — stacking two headers would look broken.
 */
export function HeaderShell() {
  const pathname = usePathname();
  if (pathname === "/") return null;
  return (
    <header className="border-b">
      <div className="mx-auto max-w-7xl px-4 py-3 flex items-center justify-between">
        <Link href="/" className="font-semibold tracking-tight">
          Market Analytics
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            local
          </span>
        </Link>
        <div className="flex items-center gap-3">
          <a
            href="http://127.0.0.1:8000/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            API docs ↗
          </a>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
