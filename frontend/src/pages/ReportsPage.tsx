import { FileBarChart } from "lucide-react";
import { Card } from "../components/Card";

export function ReportsPage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold text-slate-50">Reports</h2>
        <p className="mt-1 text-sm text-slate-400">
          Change evidence packets generated for reviewed pull requests.
        </p>
      </div>

      <Card>
        <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
          <FileBarChart className="mb-1 h-8 w-8 text-slate-600" aria-hidden="true" />
          <p className="text-sm font-medium text-slate-400">No reports generated yet.</p>
          <p className="max-w-sm text-xs text-slate-500">
            Reports are generated automatically when the Review Agent completes an AI-enriched
            analysis on a pull request. Run a review from the Pull Requests page to see one here.
          </p>
        </div>
      </Card>
    </div>
  );
}
