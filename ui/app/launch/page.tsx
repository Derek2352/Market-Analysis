import { RunLauncher } from "@/components/run-launcher";
import { RecentRuns } from "@/components/recent-runs";

export default function HomePage() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-8">
      <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-6">
        <RunLauncher />
        <RecentRuns />
      </div>
    </div>
  );
}
