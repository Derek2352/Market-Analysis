"use client";
import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PersonaCard } from "@/components/persona-card";
import { ProgressStream } from "@/components/progress-stream";
import { getPersonas, getRun } from "@/lib/api";
import type { Persona, RunDetail } from "@/lib/types";

export default function RunPage({
  params,
}: {
  params: Promise<{ run_id: string }>;
}) {
  const { run_id } = use(params);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const fetchRun = useCallback(async () => {
    try {
      const r = await getRun(run_id);
      setRun(r);
      if (r.status === "succeeded" || r.counts.personas > 0) {
        const ps = await getPersonas(run_id);
        setPersonas(ps);
      }
    } catch (e) {
      setRunError((e as Error).message);
    }
  }, [run_id]);

  useEffect(() => {
    fetchRun();
  }, [fetchRun]);

  const onPipelineDone = useCallback(() => {
    fetchRun();
  }, [fetchRun]);

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 space-y-6">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <Link
            href="/"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3 w-3" />
            New run
          </Link>
          <h1 className="text-2xl font-semibold mt-1">
            {run?.topic ?? <Skeleton className="h-7 w-48 inline-block" />}
          </h1>
          {run && (
            <p className="text-sm text-muted-foreground mt-0.5">
              Region {run.region} · {run.sources.join(", ") || "default sources"} ·
              run_id <code className="text-xs">{run.run_id}</code>
            </p>
          )}
        </div>
        {run && (
          <div className="flex items-center gap-2">
            <RunStatusBadge status={run.status} />
            {run.counts.personas > 0 && (
              <Badge variant="outline">
                {run.counts.personas} persona{run.counts.personas === 1 ? "" : "s"}
              </Badge>
            )}
            {run.counts.posts > 0 && (
              <Badge variant="outline">{run.counts.posts} posts</Badge>
            )}
          </div>
        )}
      </div>

      {runError && (
        <Card>
          <CardContent className="pt-6 text-sm text-destructive">
            Could not load run: {runError}
            <div className="text-xs text-muted-foreground mt-1">
              The backend may be down. Start it with <code>make dev-api</code>.
            </div>
          </CardContent>
        </Card>
      )}

      <ProgressStream runId={run_id} onDone={onPipelineDone} onError={onPipelineDone} />

      <Card>
        <CardHeader>
          <CardTitle>Personas</CardTitle>
        </CardHeader>
        <CardContent>
          {personas === null ? (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-44 w-full" />
              ))}
            </div>
          ) : personas.length === 0 ? (
            <div className="text-sm text-muted-foreground py-8 text-center">
              {run?.status === "succeeded"
                ? "Run finished but produced no personas. The scrape may have returned too few posts to cluster."
                : "Personas will appear here once the pipeline finishes."}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
              {personas.map((p) => (
                <PersonaCard key={p.id} runId={run_id} persona={p} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function RunStatusBadge({ status }: { status: RunDetail["status"] }) {
  const map: Record<RunDetail["status"], { variant: "success" | "destructive" | "secondary" | "muted"; label: string }> = {
    succeeded: { variant: "success", label: "succeeded" },
    failed: { variant: "destructive", label: "failed" },
    cancelled: { variant: "muted", label: "cancelled" },
    queued: { variant: "secondary", label: "queued" },
    running: { variant: "secondary", label: "running" },
  };
  const v = map[status];
  return <Badge variant={v.variant}>{v.label}</Badge>;
}
