"use client";
import { useEffect, useState } from "react";
import { ExternalLink, FileText } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { getDoc } from "@/lib/api";
import type { DocResponse } from "@/lib/types";

interface ClaimDrawerProps {
  runId: string;
  docId: string | null;
  onOpenChange: (open: boolean) => void;
}

const _cache = new Map<string, DocResponse>();

export function ClaimDrawer({ runId, docId, onOpenChange }: ClaimDrawerProps) {
  const [doc, setDoc] = useState<DocResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!docId) return;
    const cacheKey = `${runId}:${docId}`;
    if (_cache.has(cacheKey)) {
      setDoc(_cache.get(cacheKey)!);
      setError(null);
      return;
    }
    setLoading(true);
    setDoc(null);
    setError(null);
    getDoc(runId, docId)
      .then((d) => {
        _cache.set(cacheKey, d);
        setDoc(d);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runId, docId]);

  return (
    <Sheet open={!!docId} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="overflow-y-auto">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <FileText className="h-4 w-4" />
            Source quote
          </SheetTitle>
          <SheetDescription>
            The raw post behind a citation. Returned by{" "}
            <code className="text-xs">GET /runs/{"{run_id}"}/doc/{"{doc_id}"}</code>.
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-4">
          {loading && (
            <>
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-4 w-1/2" />
            </>
          )}
          {error && (
            <div className="text-sm text-destructive border border-destructive/30 bg-destructive/10 rounded-md p-3">
              {error}
            </div>
          )}
          {doc && (
            <>
              {doc.title && (
                <div className="font-medium leading-snug">{doc.title}</div>
              )}
              <blockquote className="border-l-2 border-border pl-3 text-sm leading-relaxed whitespace-pre-wrap break-words">
                {doc.body || "(empty post body)"}
              </blockquote>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="secondary">{doc.source || "unknown source"}</Badge>
                {doc.language && <Badge variant="outline">{doc.language}</Badge>}
                {doc.posted_at && (
                  <span>{new Date(doc.posted_at).toISOString().slice(0, 10)}</span>
                )}
              </div>
              {doc.url && (
                <a
                  href={doc.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline break-all"
                >
                  <ExternalLink className="h-3 w-3 shrink-0" />
                  {doc.url}
                </a>
              )}
              <div className="text-xs text-muted-foreground font-mono break-all">
                {doc.doc_id} · post_id={doc.post_id}
              </div>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
