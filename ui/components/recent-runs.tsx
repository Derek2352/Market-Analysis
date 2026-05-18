"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, CheckCircle2, Loader2, XCircle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { listRuns } from "@/lib/api";
import { relativeTime } from "@/lib/utils";
import type { RunSummary } from "@/lib/types";

export function RecentRuns() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const fetchAll = () =>
      listRuns()
        .then((rs) => alive && setRuns(rs))
        .catch((e: Error) => alive && setError(e.message));
    fetchAll();
    const t = setInterval(fetchAll, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent runs</CardTitle>
        <CardDescription>
          Polled from <code className="text-xs">GET /runs</code> every 3s. Click
          any row to view the run.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {error && (
          <div className="text-sm text-destructive border border-destructive/30 bg-destructive/10 rounded p-3">
            API unreachable: {error}
            <div className="mt-1 text-xs text-muted-foreground">
              Start the backend with <code>make dev-api</code>.
            </div>
          </div>
        )}
        {!runs && !error && (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        )}
        {runs && runs.length === 0 && (
          <div className="text-sm text-muted-foreground py-6 text-center">
            No runs yet. Start one in the form on the left.
          </div>
        )}
        {runs && runs.length > 0 && (
          <ul className="divide-y">
            {runs.map((r) => (
              <li key={r.run_id}>
                <Link
                  href={`/runs/${r.run_id}`}
                  className="flex items-center gap-3 py-2.5 hover:bg-accent/40 -mx-2 px-2 rounded"
                >
                  <StatusIcon status={r.status} />
                  <div className="min-w-0 flex-1">
                    <div className="font-medium truncate">{r.topic}</div>
                    <div className="text-xs text-muted-foreground truncate">
                      {r.region} · {r.sources.length} source{r.sources.length === 1 ? "" : "s"}
                      {r.counts.personas > 0 && ` · ${r.counts.personas} persona${r.counts.personas === 1 ? "" : "s"}`}
                    </div>
                  </div>
                  <div className="text-xs text-muted-foreground shrink-0">
                    {relativeTime(r.created_at)}
                  </div>
                  <ArrowRight className="h-4 w-4 text-muted-foreground shrink-0" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function StatusIcon({ status }: { status: RunSummary["status"] }) {
  if (status === "succeeded")
    return <CheckCircle2 className="h-4 w-4 text-success shrink-0" />;
  if (status === "failed" || status === "cancelled")
    return <XCircle className="h-4 w-4 text-destructive shrink-0" />;
  return <Loader2 className="h-4 w-4 text-primary animate-spin shrink-0" />;
}
