"use client";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { Persona } from "@/lib/types";

export function PersonaCard({ runId, persona }: { runId: string; persona: Persona }) {
  const topPainPoints = persona.pain_points.claims.slice(0, 3);
  const missingCount = persona.data_source_coverage?.categories_missing?.length ?? 0;
  const unverifiedBuckets = (
    ["goals", "motivations", "pain_points", "preferred_channels", "behaviors"] as const
  ).filter((k) => persona[k].coverage !== "ok");
  // Phase 6+ : total contested claims across all buckets — surfaced as a
  // single badge in the card footer so users can spot personas that the
  // adversarial validation pass flagged.
  const totalContested = (
    ["goals", "motivations", "pain_points", "preferred_channels", "behaviors"] as const
  ).reduce((acc, k) => {
    return acc + persona[k].claims.reduce(
      (n, c) => n + ((c.contested_by ?? []).length > 0 ? 1 : 0),
      0,
    );
  }, 0);
  const coverageTier = persona.data_source_coverage?.coverage_tier;

  const confPercent = Math.round((persona.confidence ?? 0) * 100);

  return (
    <Card className="hover:border-primary/40 transition-colors h-full flex flex-col">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <Link
            href={`/runs/${runId}/personas/${persona.id}`}
            className="font-semibold hover:underline leading-tight"
          >
            <CardTitle className="text-base">{persona.name}</CardTitle>
          </Link>
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant={confPercent >= 80 ? "success" : confPercent >= 50 ? "secondary" : "warning"}>
                {confPercent}%
              </Badge>
            </TooltipTrigger>
            <TooltipContent>Confidence (1.0 − 0.1 × unverified buckets)</TooltipContent>
          </Tooltip>
        </div>
        <p className="text-sm text-muted-foreground leading-snug pt-1">{persona.one_liner}</p>
      </CardHeader>

      <CardContent className="flex-1 flex flex-col gap-3">
        <div>
          <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-1.5">
            Top pain points ({persona.pain_points.claims.length})
          </div>
          {persona.pain_points.coverage === "unverified" ? (
            <div className="text-xs italic text-warning-foreground bg-warning/10 border border-warning/20 px-2 py-1 rounded">
              — unverified after retry —
            </div>
          ) : topPainPoints.length === 0 ? (
            <div className="text-xs italic text-muted-foreground">No pain points surfaced.</div>
          ) : (
            <ul className="text-sm space-y-1.5">
              {topPainPoints.map((p, i) => (
                <li key={i} className="flex items-start gap-2 leading-snug">
                  <span
                    className={`mt-1.5 inline-block h-1.5 w-1.5 rounded-full shrink-0 ${
                      p.severity === "high"
                        ? "bg-destructive"
                        : p.severity === "medium"
                          ? "bg-warning"
                          : "bg-muted-foreground"
                    }`}
                  />
                  <span className="line-clamp-2">{p.claim}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-2 border-t">
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge
                variant={
                  coverageTier === "high"
                    ? "success"
                    : coverageTier === "balanced"
                      ? "secondary"
                      : coverageTier === "limited"
                        ? "muted"
                        : missingCount === 0
                          ? "success"
                          : "warning"
                }
                className="cursor-help"
              >
                {coverageTier
                  ? coverageTier
                  : `${persona.data_source_coverage.categories_present.length}/${
                      persona.data_source_coverage.categories_present.length + missingCount
                    } categories`}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>{persona.data_source_coverage.bias_warning}</TooltipContent>
          </Tooltip>
          <Badge variant="outline">{persona.cluster_size} posts</Badge>
          {unverifiedBuckets.length > 0 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="warning" className="cursor-help">
                  {unverifiedBuckets.length} unverified
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                Unverified buckets: {unverifiedBuckets.join(", ")}
              </TooltipContent>
            </Tooltip>
          )}
          {totalContested > 0 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge variant="warning" className="cursor-help">
                  ⚠ {totalContested} contested
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                Claims flagged by adversarial validation: {totalContested}
              </TooltipContent>
            </Tooltip>
          )}
          <Link
            href={`/runs/${runId}/personas/${persona.id}`}
            className="ml-auto text-xs text-primary hover:underline"
          >
            View →
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
