import type { Metadata } from "next";
import Link from "next/link";
import { ThemeToggle } from "@/components/theme-toggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "Market Analytics — Local",
  description: "Personas and journey maps from public web data. Local only.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning className="h-full antialiased">
      {/* Inline pre-paint script: pick theme from localStorage or system,
          set <html class="dark"|"light"> before React hydrates to avoid FOUC. */}
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('theme');var d=t==='dark'||(!t&&window.matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d);document.documentElement.classList.toggle('light',!d);}catch(e){}})();`,
          }}
        />
      </head>
      <body className="min-h-full flex flex-col bg-background text-foreground">
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
        <main className="flex-1">{children}</main>
        <footer className="border-t mt-8">
          <div className="mx-auto max-w-7xl px-4 py-3 text-xs text-muted-foreground">
            localhost only · no telemetry · pipeline runs on the FastAPI backend
            at <code>127.0.0.1:8000</code>
          </div>
        </footer>
      </body>
    </html>
  );
}
