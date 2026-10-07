import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output produces .next/standalone/server.js — a self-contained
  // Node bundle that doesn't need node_modules at runtime. The Windows
  // launcher uses this so the shipped folder stays small.
  output: "standalone",
};

export default nextConfig;
