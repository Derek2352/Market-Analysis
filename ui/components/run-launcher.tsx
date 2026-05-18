"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { createRun } from "@/lib/api";
import type { Provider, Region } from "@/lib/types";

const REGIONS: { value: Region; label: string; sources: string[] }[] = [
  { value: "HK", label: "Hong Kong", sources: ["lihkg", "openrice", "app_store_hk", "google_play_hk", "reddit_old"] },
  { value: "US", label: "United States", sources: ["reddit_old", "app_store_hk"] },
  { value: "TW", label: "Taiwan", sources: ["reddit_old"] },
  { value: "JP", label: "Japan", sources: ["reddit_old"] },
];

const SINCE_PRESETS = [30, 90, 180, 365];

export function RunLauncher() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [region, setRegion] = useState<Region>("HK");
  const [selectedSources, setSelectedSources] = useState<string[]>([
    "lihkg",
    "app_store_hk",
    "google_play_hk",
  ]);
  const [sinceDays, setSinceDays] = useState<number>(90);
  const [provider, setProvider] = useState<Provider>("deepseek");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const region_cfg = REGIONS.find((r) => r.value === region)!;

  const toggleSource = (s: string) => {
    setSelectedSources((arr) =>
      arr.includes(s) ? arr.filter((x) => x !== s) : [...arr, s],
    );
  };

  const onRegionChange = (next: Region) => {
    setRegion(next);
    const next_cfg = REGIONS.find((r) => r.value === next)!;
    setSelectedSources(next_cfg.sources.slice(0, 3));
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const r = await createRun({
        topic: topic.trim(),
        region,
        sources: selectedSources,
        since_days: sinceDays,
        provider,
      });
      router.push(`/runs/${r.run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setSubmitting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Generate personas + journey maps</CardTitle>
        <CardDescription>
          Scrapes the selected sources, embeds with BGE-M3, clusters with
          UMAP+HDBSCAN, then synthesizes one Persona + Journey per cluster.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="topic">Topic, brand, or product</Label>
            <Input
              id="topic"
              required
              autoFocus
              placeholder='e.g. "MTR Mobile" or "Octopus card"'
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
          </div>

          <div className="space-y-2">
            <Label>Region</Label>
            <div className="flex flex-wrap gap-2">
              {REGIONS.map((r) => (
                <button
                  key={r.value}
                  type="button"
                  onClick={() => onRegionChange(r.value)}
                  className={`px-3 py-1.5 rounded-md text-sm border transition-colors ${
                    region === r.value
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-background hover:bg-accent"
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>Sources</Label>
            <div className="flex flex-wrap gap-2">
              {region_cfg.sources.map((s) => {
                const on = selectedSources.includes(s);
                return (
                  <button
                    key={s}
                    type="button"
                    onClick={() => toggleSource(s)}
                    className={`px-3 py-1.5 rounded-md text-xs font-mono border transition-colors ${
                      on
                        ? "bg-secondary text-secondary-foreground border-border"
                        : "bg-background text-muted-foreground hover:bg-accent"
                    }`}
                  >
                    {on ? "✓" : "○"} {s}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex justify-between">
              <Label htmlFor="since">Look-back window</Label>
              <span className="text-sm text-muted-foreground">{sinceDays} days</span>
            </div>
            <input
              id="since"
              type="range"
              min={30}
              max={365}
              step={1}
              value={sinceDays}
              onChange={(e) => setSinceDays(Number(e.target.value))}
              className="w-full accent-primary"
            />
            <div className="flex justify-between text-xs text-muted-foreground">
              {SINCE_PRESETS.map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setSinceDays(d)}
                  className="hover:text-foreground"
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <Label>LLM provider</Label>
            <div className="flex gap-2">
              {(["anthropic", "deepseek"] as Provider[]).map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setProvider(p)}
                  className={`px-3 py-1.5 rounded-md text-sm border transition-colors ${
                    provider === p
                      ? "bg-primary text-primary-foreground border-primary"
                      : "bg-background hover:bg-accent"
                  }`}
                >
                  {p === "anthropic" ? "Anthropic (Sonnet 4.6)" : "DeepSeek (chat)"}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              DeepSeek is ~10x cheaper. Both honor the $4 per-run cost cap.
            </p>
          </div>

          {error && (
            <div className="text-sm text-destructive border border-destructive/30 bg-destructive/10 rounded p-2">
              {error}
            </div>
          )}

          <Button type="submit" disabled={submitting || !topic.trim()} className="w-full">
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Starting…
              </>
            ) : (
              <>Start run →</>
            )}
          </Button>
          {selectedSources.length === 0 && (
            <p className="text-xs text-muted-foreground text-center">
              No sources selected — the run will use the region default list.
            </p>
          )}
        </form>
      </CardContent>
    </Card>
  );
}
