"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { CoverageBanner } from "@/components/coverage-banner";
import { JourneyGrid } from "@/components/journey-grid";
import { getJourney, getPersonas } from "@/lib/api";
import type { JourneyMap, Persona } from "@/lib/types";

export default function JourneyPage({
  params,
}: {
  params: Promise<{ run_id: string; persona_id: string }>;
}) {
  const { run_id, persona_id } = use(params);
  const [persona, setPersona] = useState<Persona | null>(null);
  const [journey, setJourney] = useState<JourneyMap | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getPersonas(run_id), getJourney(run_id, persona_id)])
      .then(([ps, j]) => {
        setPersona(ps.find((p) => p.id === persona_id) ?? null);
        setJourney(j);
      })
      .catch((e: Error) => setError(e.message));
  }, [run_id, persona_id]);

  if (error) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-8">
        <Link
          href={`/runs/${run_id}/personas/${persona_id}`}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-3 w-3" />
          Back
        </Link>
        <Card className="mt-3">
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      </div>
    );
  }

  if (!journey || !persona) {
    return (
      <div className="mx-auto max-w-7xl px-4 py-6 space-y-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-16 w-full" />
        <div className="grid grid-cols-6 gap-3 mt-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-96 w-full" />
          ))}
        </div>
      </div>
    );
  }

  const thinStages = journey.stages.filter((s) => s.coverage !== "ok").map((s) => s.stage);

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            href={`/runs/${run_id}/personas/${persona_id}`}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3 w-3" />
            Back to {persona.name}
          </Link>
          <h1 className="text-2xl font-semibold mt-1">Journey map</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            {persona.name} — {persona.one_liner}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {thinStages.length > 0 && (
            <Badge variant="warning">
              {thinStages.length} stage{thinStages.length === 1 ? "" : "s"} with limited data
            </Badge>
          )}
          <Badge variant="outline">{journey.provider} / {journey.model}</Badge>
        </div>
      </div>

      <CoverageBanner coverage={journey.data_source_coverage} />

      <div className="hidden lg:block text-xs text-muted-foreground italic">
        Click any • or emotion bar to open the source quote.
      </div>

      <JourneyGrid runId={run_id} journey={journey} />
    </div>
  );
}
