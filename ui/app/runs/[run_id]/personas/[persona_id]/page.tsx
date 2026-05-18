"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ExternalLink, MapPin } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { CoverageBanner } from "@/components/coverage-banner";
import { getPersonas } from "@/lib/api";
import type { ClaimList, Persona } from "@/lib/types";

export default function PersonaPage({
  params,
}: {
  params: Promise<{ run_id: string; persona_id: string }>;
}) {
  const { run_id, persona_id } = use(params);
  const [persona, setPersona] = useState<Persona | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPersonas(run_id)
      .then((ps) => {
        const p = ps.find((x) => x.id === persona_id) ?? null;
        if (!p) setError(`Persona ${persona_id} not found in run ${run_id}.`);
        setPersona(p);
      })
      .catch((e: Error) => setError(e.message));
  }, [run_id, persona_id]);

  if (error) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-8">
        <Link href={`/runs/${run_id}`} className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-3 w-3" />
          Back to run
        </Link>
        <Card className="mt-3">
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      </div>
    );
  }

  if (!persona) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-8 space-y-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const confPct = Math.round((persona.confidence ?? 0) * 100);
  const demo = persona.demographics as { age_range?: string; occupation_examples?: string[] };
  return (
    <div className="mx-auto max-w-5xl px-4 py-6 space-y-6">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <Link
            href={`/runs/${run_id}`}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3 w-3" />
            Back to run
          </Link>
          <h1 className="text-2xl font-semibold mt-1">{persona.name}</h1>
          <p className="text-base text-muted-foreground mt-1">{persona.one_liner}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={confPct >= 80 ? "success" : confPct >= 50 ? "secondary" : "warning"}>
            {confPct}% confidence
          </Badge>
          <Badge variant="outline">{persona.cluster_size} posts</Badge>
          <Link
            href={`/runs/${run_id}/journeys/${persona.id}`}
            className="inline-flex items-center gap-1 rounded-md bg-primary text-primary-foreground px-3 py-1.5 text-sm hover:bg-primary/90"
          >
            <MapPin className="h-3.5 w-3.5" />
            View journey map
          </Link>
        </div>
      </div>

      <CoverageBanner coverage={persona.data_source_coverage} />

      {(demo.age_range || demo.occupation_examples?.length) && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Demographics</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              {demo.age_range && <Badge variant="secondary">age {demo.age_range}</Badge>}
              {(demo.occupation_examples ?? []).map((o, i) => (
                <Badge key={i} variant="outline">
                  {o}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ClaimSection title="Goals" list={persona.goals} />
        <ClaimSection title="Motivations" list={persona.motivations} />
      </div>

      <ClaimSection title="Pain points" list={persona.pain_points} showSeverity />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <ClaimSection title="Preferred channels" list={persona.preferred_channels} />
        <ClaimSection title="Behaviors" list={persona.behaviors} />
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">
            Representative quotes ({persona.representative_quotes.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          {persona.representative_quotes.length === 0 ? (
            <div className="text-sm text-muted-foreground italic">No quotes returned.</div>
          ) : (
            <ul className="space-y-4">
              {persona.representative_quotes.map((q, i) => (
                <li key={i} className="border-l-2 border-border pl-3">
                  <div className="text-sm leading-relaxed whitespace-pre-wrap">{q.text_original}</div>
                  {q.text_translated && (
                    <div className="text-xs text-muted-foreground mt-1 italic">
                      “{q.text_translated}”
                    </div>
                  )}
                  <div className="flex flex-wrap items-center gap-2 mt-2 text-xs text-muted-foreground">
                    {q.source && <Badge variant="secondary">{q.source}</Badge>}
                    {q.lang && <Badge variant="outline">{q.lang}</Badge>}
                    {q.url && (
                      <a
                        href={q.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-primary hover:underline break-all"
                      >
                        <ExternalLink className="h-3 w-3" />
                        {q.url}
                      </a>
                    )}
                    <span className="font-mono text-[10px] opacity-60">{q.doc_id}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <div className="text-xs text-muted-foreground">
        Synthesized by {persona.provider} / {persona.model}
        {persona.generated_at && ` · ${new Date(persona.generated_at).toISOString().slice(0, 19)}Z`}
      </div>
    </div>
  );
}

function ClaimSection({
  title,
  list,
  showSeverity = false,
}: {
  title: string;
  list: ClaimList;
  showSeverity?: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-2 flex flex-row items-center justify-between">
        <CardTitle className="text-base">
          {title} <span className="text-muted-foreground font-normal">({list.claims.length})</span>
        </CardTitle>
        {list.coverage !== "ok" && (
          <Badge variant="warning">{list.coverage}</Badge>
        )}
      </CardHeader>
      <CardContent>
        {list.coverage === "unverified" ? (
          <div className="text-xs italic text-warning-foreground bg-warning/10 border border-warning/20 rounded px-2 py-1.5">
            Claims dropped after retry — could not be grounded in the evidence pack.
          </div>
        ) : list.claims.length === 0 ? (
          <div className="text-sm text-muted-foreground italic">No items.</div>
        ) : (
          <ul className="space-y-1.5">
            {list.claims.map((c, i) => (
              <li key={i} className="flex items-start gap-2 text-sm leading-snug">
                <span
                  className={`mt-1.5 inline-block h-1.5 w-1.5 rounded-full shrink-0 ${
                    showSeverity && c.severity === "high"
                      ? "bg-destructive"
                      : showSeverity && c.severity === "medium"
                        ? "bg-warning"
                        : "bg-muted-foreground/60"
                  }`}
                />
                <span>
                  {c.claim}
                  {showSeverity && c.severity && (
                    <span className="ml-1.5 text-xs text-muted-foreground">
                      ({c.severity})
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
