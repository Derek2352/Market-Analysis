import { Suspense } from "react";
import { RunLauncher } from "@/components/run-launcher";
import { RecentRuns } from "@/components/recent-runs";

export default function LaunchPage() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-6">
        {/* useSearchParams() inside RunLauncher needs a Suspense boundary
            so the page can still be statically prerendered. */}
        <Suspense fallback={null}>
          <RunLauncher />
        </Suspense>
        <RecentRuns />
      </div>
    </div>
  );
}
