import type { Metadata } from "next";
import { JetBrains_Mono, Newsreader } from "next/font/google";
import { HeaderShell } from "@/components/header-shell";
import "./globals.css";

// Build-time-downloaded fonts — no runtime CDN, fits the project's
// offline-first policy. JetBrains Mono carries source ids, run ids,
// CLI commands, file paths. Newsreader italic provides the editorial
// emphasis the landing design calls for.
const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

const serif = Newsreader({
  subsets: ["latin"],
  weight: ["400", "500"],
  style: ["normal", "italic"],
  variable: "--font-serif",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Market Analytics — Local",
  description: "Personas and journey maps from public web data. Local only.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`h-full antialiased ${mono.variable} ${serif.variable}`}
    >
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
        {/* HeaderShell hides itself on the landing route (`/`) so the
            landing page's own nav can take over without doubling up. */}
        <HeaderShell />
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
