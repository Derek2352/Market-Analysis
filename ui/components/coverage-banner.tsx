"use client";
import { AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { DataSourceCoverage } from "@/lib/types";

export function CoverageBanner({ coverage }: { coverage: DataSourceCoverage }) {
  const missing = coverage.categories_missing ?? [];
  const present = coverage.categories_present ?? [];
  const showWarning = missing.length > 0;
  return (
    <div
      className={`rounded-lg border p-3 text-sm ${
        showWarning
          ? "border-warning/30 bg-warning/10 text-foreground"
          : "border-success/30 bg-success/10 text-foreground"
      }`}
    >
      <div className="flex flex-wrap items-start gap-3">
        {showWarning && <AlertTriangle className="h-4 w-4 mt-0.5 text-warning shrink-0" />}
        <div className="flex-1">
          <div className="font-medium leading-tight">
            {showWarning ? "Limited data coverage" : "Balanced coverage"}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            <strong>Present:</strong>{" "}
            {present.length ? present.join(", ") : "none"}
            {missing.length > 0 && (
              <>
                {" · "}
                <strong>Missing:</strong> {missing.join(", ")}
              </>
            )}
          </div>
          <div className="mt-1 text-xs">{coverage.bias_warning}</div>
        </div>
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="outline" className="cursor-help">
                {coverage.sources_used.length} source{coverage.sources_used.length === 1 ? "" : "s"}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              <div className="space-y-1">
                {Object.entries(coverage.doc_counts).map(([src, n]) => (
                  <div key={src} className="text-xs">
                    {src}: {n}
                  </div>
                ))}
              </div>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    </div>
  );
}
