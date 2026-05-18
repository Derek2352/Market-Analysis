"use client";
import { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { streamUrl } from "@/lib/api";
import type { PipelineStage } from "@/lib/types";

const STAGE_ORDER: PipelineStage[] = ["scrape", "embed", "cluster", "synthesize"];
const STAGE_LABEL: Record<PipelineStage, string> = {
  scrape: "Scraping",
  embed: "Embedding",
  cluster: "Clustering",
  synthesize: "Synthesizing",
};

type StageState = { status: "pending" | "running" | "done"; message: string; pct: number };

interface ProgressStreamProps {
  runId: string;
  onDone?: () => void;
  onError?: (msg: string) => void;
}

export function ProgressStream({ runId, onDone, onError }: ProgressStreamProps) {
  const initial = useMemo<Record<PipelineStage, StageState>>(
    () => ({
      scrape: { status: "pending", message: "", pct: 0 },
      embed: { status: "pending", message: "", pct: 0 },
      cluster: { status: "pending", message: "", pct: 0 },
      synthesize: { status: "pending", message: "", pct: 0 },
    }),
    [],
  );
  const [stages, setStages] = useState(initial);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [terminal, setTerminal] = useState<"done" | "error" | "cancelled" | null>(null);
  const [errorMsg, setErrorMsg] = useState<string>("");

  useEffect(() => {
    const es = new EventSource(streamUrl(runId));

    const handler = (kind: string) => (ev: MessageEvent) => {
      let data: Record<string, unknown> = {};
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (kind === "queued") return;
      if (kind === "stage_start") {
        const stage = data.stage as PipelineStage;
        setStages((s) => ({
          ...s,
          [stage]: { ...s[stage], status: "running", message: String(data.message ?? "") },
        }));
      } else if (kind === "progress") {
        const stage = data.stage as PipelineStage;
        const msg = String(data.message ?? "");
        const pct = Number(data.pct ?? 0);
        if (msg.startsWith("WARNING") || msg.startsWith("NOTICE")) {
          setWarnings((w) => (w.includes(msg) ? w : [...w, msg]));
          return;
        }
        setStages((s) => ({
          ...s,
          [stage]: { ...s[stage], status: "running", pct, message: msg },
        }));
      } else if (kind === "stage_done") {
        const stage = data.stage as PipelineStage;
        setStages((s) => ({
          ...s,
          [stage]: { status: "done", pct: 1, message: String(data.message ?? "") },
        }));
      } else if (kind === "done") {
        setTerminal("done");
        es.close();
        onDone?.();
      } else if (kind === "error") {
        const msg = String(data.error ?? "Unknown error");
        setTerminal("error");
        setErrorMsg(msg);
        es.close();
        onError?.(msg);
      } else if (kind === "cancelled") {
        setTerminal("cancelled");
        es.close();
      }
    };

    es.addEventListener("queued", handler("queued"));
    es.addEventListener("stage_start", handler("stage_start"));
    es.addEventListener("progress", handler("progress"));
    es.addEventListener("stage_done", handler("stage_done"));
    es.addEventListener("done", handler("done"));
    es.addEventListener("error", handler("error"));
    es.addEventListener("cancelled", handler("cancelled"));

    return () => es.close();
  }, [runId, onDone, onError]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <CardTitle>Pipeline progress</CardTitle>
          {terminal === "done" && <Badge variant="success">complete</Badge>}
          {terminal === "error" && <Badge variant="destructive">failed</Badge>}
          {terminal === "cancelled" && <Badge variant="muted">cancelled</Badge>}
          {!terminal && <Badge variant="secondary">live</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {STAGE_ORDER.map((stage) => {
          const s = stages[stage];
          return (
            <div key={stage} className="space-y-1.5">
              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2">
                  <span
                    className={
                      s.status === "done"
                        ? "text-success font-medium"
                        : s.status === "running"
                          ? "text-primary font-medium"
                          : "text-muted-foreground"
                    }
                  >
                    {STAGE_LABEL[stage]}
                  </span>
                  {s.status === "done" && <span className="text-success text-xs">✓</span>}
                </div>
                {s.message && (
                  <span className="text-xs text-muted-foreground truncate ml-3 max-w-[60%]">
                    {s.message}
                  </span>
                )}
              </div>
              <Progress value={s.status === "done" ? 100 : Math.round(s.pct * 100)} />
            </div>
          );
        })}

        {warnings.length > 0 && (
          <div className="mt-3 space-y-1">
            {warnings.map((w, i) => (
              <div
                key={i}
                className="text-xs border border-warning/30 bg-warning/10 rounded p-2 leading-snug"
              >
                {w}
              </div>
            ))}
          </div>
        )}

        {terminal === "error" && (
          <div className="text-sm border border-destructive/30 bg-destructive/10 rounded p-3 text-destructive">
            {errorMsg || "Pipeline failed."}
          </div>
        )}

        {!terminal && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground pt-2">
            <Skeleton className="h-2 w-2 rounded-full" />
            <span>Tailing SSE stream from /runs/{runId}/stream…</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
