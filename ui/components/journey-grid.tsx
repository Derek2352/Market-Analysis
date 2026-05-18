"use client";
import { useState } from "react";
import {
  AlertTriangle,
  Lightbulb,
  MapPin,
  MessageSquare,
  Smile,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ClaimDrawer } from "@/components/claim-drawer";
import { cn, intensityBar } from "@/lib/utils";
import type {
  ClaimList,
  EvidenceClaim,
  EmotionPoint,
  JourneyMap,
  JourneyStage,
} from "@/lib/types";

const STAGE_ORDER: JourneyStage["stage"][] = [
  "Awareness",
  "Consideration",
  "Decision",
  "Onboarding",
  "Use",
  "Loyalty/Churn",
];

type RowKind = "touchpoints" | "user_actions" | "emotions" | "frictions" | "opportunities";

const ROWS: { key: RowKind; label: string; Icon: React.ComponentType<{ className?: string }>; tone: string }[] = [
  { key: "touchpoints", label: "Touchpoints", Icon: MapPin, tone: "text-blue-500 dark:text-blue-400" },
  { key: "user_actions", label: "User actions", Icon: MessageSquare, tone: "text-foreground" },
  { key: "emotions", label: "Emotions", Icon: Smile, tone: "text-purple-500 dark:text-purple-400" },
  { key: "frictions", label: "Frictions", Icon: AlertTriangle, tone: "text-warning" },
  { key: "opportunities", label: "Opportunities", Icon: Lightbulb, tone: "text-success" },
];

export function JourneyGrid({ runId, journey }: { runId: string; journey: JourneyMap }) {
  const [activeDoc, setActiveDoc] = useState<string | null>(null);

  // Index stages by name so we always render in canonical order even if the
  // backend returns them out-of-order.
  const stagesByName = new Map<string, JourneyStage>(
    journey.stages.map((s) => [s.stage, s]),
  );

  return (
    <>
      <div className="overflow-x-auto pb-2">
        <div
          className="grid gap-3 min-w-fit"
          style={{ gridTemplateColumns: `repeat(${STAGE_ORDER.length}, minmax(220px, 1fr))` }}
        >
          {/* Stage headers (sticky on Y scroll) */}
          {STAGE_ORDER.map((stage) => {
            const s = stagesByName.get(stage);
            const cov = s?.coverage ?? "none";
            const limited = cov === "thin" || cov === "none";
            return (
              <div
                key={stage}
                className={cn(
                  "rounded-t-md border border-b-0 px-3 py-2 font-semibold text-sm sticky top-0 z-10",
                  limited ? "bg-muted/40 text-muted-foreground" : "bg-secondary text-secondary-foreground",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span>{stage}</span>
                  {limited && (
                    <Badge variant="warning" className="text-[10px] px-1.5 py-0">
                      {cov === "none" ? "no data" : "limited"}
                    </Badge>
                  )}
                </div>
              </div>
            );
          })}

          {/* Row cells (one row per dimension, one cell per stage column) */}
          {ROWS.map((row) =>
            STAGE_ORDER.map((stage, stageIdx) => {
              const s = stagesByName.get(stage);
              const stageLimited = (s?.coverage ?? "none") !== "ok";
              const isLastRow = ROWS[ROWS.length - 1].key === row.key;
              const isFirstColInRow = stageIdx === 0;

              return (
                <div
                  key={`${row.key}:${stage}`}
                  className={cn(
                    "border-x border-b p-3 relative",
                    stageLimited && "bg-muted/30",
                    isLastRow && "rounded-b-md",
                  )}
                >
                  {/* Row label in the first column, badge-style */}
                  {isFirstColInRow && (
                    <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground mb-1.5">
                      <row.Icon className={cn("h-3 w-3", row.tone)} />
                      <span>{row.label}</span>
                    </div>
                  )}
                  <CellContent
                    row={row.key}
                    stage={s}
                    onClickDoc={setActiveDoc}
                  />
                </div>
              );
            }),
          )}
        </div>
      </div>

      <ClaimDrawer
        runId={runId}
        docId={activeDoc}
        onOpenChange={(open) => !open && setActiveDoc(null)}
      />
    </>
  );
}

function CellContent({
  row,
  stage,
  onClickDoc,
}: {
  row: RowKind;
  stage: JourneyStage | undefined;
  onClickDoc: (docId: string) => void;
}) {
  if (!stage) {
    return <div className="text-xs italic text-muted-foreground">no data</div>;
  }
  if (row === "emotions") {
    const list: EmotionPoint[] = stage.emotions ?? [];
    if (!list.length) {
      return <div className="text-xs italic text-muted-foreground">—</div>;
    }
    return (
      <ul className="space-y-1">
        {list.map((e, i) => (
          <li key={i} className="text-xs">
            <button
              onClick={() => e.evidence?.[0] && onClickDoc(e.evidence[0])}
              disabled={!e.evidence?.length}
              className="text-left w-full hover:bg-accent/50 rounded px-1.5 py-0.5 -mx-1.5 disabled:cursor-default disabled:hover:bg-transparent"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="capitalize">{e.label}</span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {intensityBar(e.intensity)}
                </span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    );
  }
  const cl: ClaimList = stage[row];
  if (cl.coverage === "unverified") {
    return (
      <div className="text-[11px] italic text-warning-foreground bg-warning/10 border border-warning/20 rounded px-1.5 py-1">
        — unverified —
      </div>
    );
  }
  if (!cl.claims.length) {
    return <div className="text-xs italic text-muted-foreground">—</div>;
  }
  return (
    <ul className="space-y-1">
      {cl.claims.map((c, i) => (
        <ClaimRow key={i} claim={c} onClickDoc={onClickDoc} />
      ))}
    </ul>
  );
}

function ClaimRow({
  claim,
  onClickDoc,
}: {
  claim: EvidenceClaim;
  onClickDoc: (docId: string) => void;
}) {
  const docId = claim.evidence?.[0];
  return (
    <li className="text-xs leading-snug">
      <button
        onClick={() => docId && onClickDoc(docId)}
        disabled={!docId}
        className="flex items-start gap-1.5 w-full text-left hover:bg-accent/50 rounded px-1.5 py-0.5 -mx-1.5 disabled:cursor-default disabled:hover:bg-transparent"
      >
        <span
          className={cn(
            "mt-1 inline-block h-1.5 w-1.5 rounded-full shrink-0",
            claim.severity === "high"
              ? "bg-destructive"
              : claim.severity === "medium"
                ? "bg-warning"
                : "bg-muted-foreground/60",
          )}
        />
        <span className="line-clamp-3">{claim.claim}</span>
      </button>
    </li>
  );
}
