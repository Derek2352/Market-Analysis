"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { createRun, listRegions } from "@/lib/api";
import type { Provider, RegionInfo, SourceInfo } from "@/lib/types";

const SINCE_PRESETS = [30, 90, 180, 365];

export function RunLauncher() {
  const router = useRouter();
  // Pre-fill from URL when the landing's "Start run →" link passes
  // ?topic=&region=&sources=&provider=. Each param is optional.
  const searchParams = useSearchParams();
  const urlTopic    = searchParams.get("topic")    ?? "";
  const urlRegion   = searchParams.get("region");
  const urlSources  = searchParams.get("sources"); // comma-separated
  const urlProvider = searchParams.get("provider"); // "anthropic" | "deepseek"

  const [regions, setRegions] = useState<RegionInfo[] | null>(null);
  const [regionsError, setRegionsError] = useState<string | null>(null);
  const [topic, setTopic] = useState(urlTopic);
  const [regionId, setRegionId] = useState<string>(urlRegion ?? "HK");
  const [selectedSources, setSelectedSources] = useState<Set<string>>(new Set());
  const [sinceDays, setSinceDays] = useState<number>(90);
  const [provider, setProvider] = useState<Provider>(
    urlProvider === "anthropic" || urlProvider === "deepseek"
      ? urlProvider
      : "deepseek",
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [acceptTosRisk, setAcceptTosRisk] = useState(false);

  // Fetch regions on mount. URL params win over the auto-select when
  // present — that's what the landing's "Start run →" CTA expects.
  useEffect(() => {
    listRegions()
      .then((rs) => {
        setRegions(rs);
        const target =
          (urlRegion && rs.find((r) => r.region_id === urlRegion)) ||
          rs.find((r) => r.region_id === "HK") ||
          rs[0];
        if (!target) return;
        setRegionId(target.region_id);

        if (urlSources) {
          // Trust the URL list — keep only ids that exist in this region.
          const valid = new Set([
            ...target.default_sources.map((s) => s.source_id),
            ...target.opt_in_sources.map((s) => s.source_id),
          ]);
          const picked = urlSources
            .split(",")
            .map((s) => s.trim())
            .filter((s) => valid.has(s));
          setSelectedSources(new Set(picked.length ? picked : target.default_sources.slice(0, 5).map((s) => s.source_id)));
        } else {
          setSelectedSources(
            new Set(target.default_sources.slice(0, 5).map((s) => s.source_id)),
          );
        }
      })
      .catch((e: Error) => setRegionsError(e.message));
    // Only run once on mount; URL params are read once. Re-running on
    // URL change would clobber the user's in-page edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentRegion = regions?.find((r) => r.region_id === regionId);
  const allSources: SourceInfo[] = currentRegion
    ? [...currentRegion.default_sources, ...currentRegion.opt_in_sources]
    : [];

  // Track whether the user has any opt-in sources active.
  const optInActive = allSources.some(
    (s) => selectedSources.has(s.source_id) && !s.default_enabled,
  );

  const onRegionChange = (next: string) => {
    setRegionId(next);
    const r = regions?.find((x) => x.region_id === next);
    if (r) {
      setSelectedSources(
        new Set(r.default_sources.slice(0, 5).map((s) => s.source_id)),
      );
    }
  };

  const toggleSource = (sourceId: string) => {
    setSelectedSources((prev) => {
      const next = new Set(prev);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!topic.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const r = await createRun({
        topic: topic.trim(),
        region: regionId,
        sources: Array.from(selectedSources),
        since_days: sinceDays,
        provider,
        // No --accept-tos-risk in the API payload yet; the CLI emits a
        // warning that's purely informational. UI surfaces it pre-submit.
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
            {regionsError && (
              <div className="text-xs text-destructive">
                Couldn't load regions: {regionsError}. Start the backend with{" "}
                <code>make dev-api</code>.
              </div>
            )}
            {!regions && !regionsError && (
              <div className="flex gap-2">
                <Skeleton className="h-9 w-28" />
                <Skeleton className="h-9 w-32" />
                <Skeleton className="h-9 w-20" />
                <Skeleton className="h-9 w-20" />
              </div>
            )}
            {regions && (
              <div className="flex flex-wrap gap-2">
                {regions.map((r) => {
                  const total = r.default_sources.length + r.opt_in_sources.length;
                  return (
                    <button
                      key={r.region_id}
                      type="button"
                      onClick={() => onRegionChange(r.region_id)}
                      className={`px-3 py-1.5 rounded-md text-sm border transition-colors ${
                        regionId === r.region_id
                          ? "bg-primary text-primary-foreground border-primary"
                          : "bg-background hover:bg-accent"
                      }`}
                    >
                      {r.display_name}{" "}
                      <span className="opacity-60 text-xs">({total})</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="space-y-2">
            <Label>Sources</Label>
            {currentRegion && (
              <p className="text-xs text-muted-foreground">
                {currentRegion.default_sources.length} default-on,{" "}
                {currentRegion.opt_in_sources.length} opt-in (ToS-prohibited).
                Languages: {currentRegion.primary_languages.join(", ")}.
              </p>
            )}
            <TooltipProvider>
              <div className="flex flex-wrap gap-2">
                {allSources.map((s) => {
                  const on = selectedSources.has(s.source_id);
                  const prohibited =
                    s.tos_scraping_stance === "prohibited";
                  return (
                    <Tooltip key={s.source_id}>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          onClick={() => toggleSource(s.source_id)}
                          className={`px-3 py-1.5 rounded-md text-xs font-mono border transition-colors ${
                            on
                              ? prohibited
                                ? "bg-warning/15 text-warning-foreground border-warning/40"
                                : "bg-secondary text-secondary-foreground border-border"
                              : "bg-background text-muted-foreground hover:bg-accent"
                          }`}
                        >
                          {on ? "✓" : "○"} {s.source_id}
                          {prohibited && (
                            <span className="ml-1 text-[10px] opacity-80">
                              ⚠
                            </span>
                          )}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>
                        <div className="text-xs">
                          <div className="font-medium">{s.category}</div>
                          {prohibited && (
                            <div className="text-warning-foreground">
                              ToS prohibits scraping — opt-in only
                            </div>
                          )}
                          {!prohibited && s.tos_scraping_stance && (
                            <div className="text-muted-foreground">
                              ToS: {s.tos_scraping_stance}
                            </div>
                          )}
                          {s.notes && (
                            <div className="text-muted-foreground mt-1 max-w-xs whitespace-normal">
                              {s.notes}
                            </div>
                          )}
                        </div>
                      </TooltipContent>
                    </Tooltip>
                  );
                })}
              </div>
            </TooltipProvider>
          </div>

          {optInActive && (
            <div className="border border-warning/40 bg-warning/10 rounded p-2 text-xs">
              <div className="font-medium">⚠ Opt-in sources active</div>
              <div className="mt-1 text-muted-foreground">
                You've enabled one or more sources whose Terms of Service
                prohibit automated access. Use of this tool for commercial
                purposes against those sources may violate their terms — you
                accept that responsibility.
              </div>
              <label className="mt-2 flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={acceptTosRisk}
                  onChange={(e) => setAcceptTosRisk(e.target.checked)}
                  className="accent-warning"
                />
                I understand and accept the risk.
              </label>
            </div>
          )}

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
              DeepSeek is ~10x cheaper.
            </p>
          </div>

          {error && (
            <div className="text-sm text-destructive border border-destructive/30 bg-destructive/10 rounded p-2">
              {error}
            </div>
          )}

          <Button
            type="submit"
            disabled={
              submitting ||
              !topic.trim() ||
              selectedSources.size === 0 ||
              (optInActive && !acceptTosRisk)
            }
            className="w-full"
          >
            {submitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Starting…
              </>
            ) : (
              <>Start run →</>
            )}
          </Button>
          {selectedSources.size === 0 && (
            <p className="text-xs text-muted-foreground text-center">
              Pick at least one source to start.
            </p>
          )}
        </form>
      </CardContent>
    </Card>
  );
}
