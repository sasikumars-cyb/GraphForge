import { Card } from "../Card";
import { CollapsibleSection } from "./CollapsibleSection";
import { BarChart } from "./BarChart";
import type { ChartData } from "../../lib/executiveReportMapper";

interface PerformanceChartsProps {
  data: ChartData;
}

/**
 * Performance charts section — execution time, token usage, and cost by stage
 * rendered as pure CSS horizontal bar charts.
 */
export function PerformanceCharts({ data }: PerformanceChartsProps) {
  const hasData = data.duration.length > 0;
  if (!hasData) return null;

  return (
    <CollapsibleSection title="Performance Charts">
      <Card>
        <BarChart title="Execution Time by Stage" bars={data.duration} barColor="bg-accent-solid" />
        <BarChart title="Token Usage by Stage" bars={data.tokens} barColor="bg-info-solid" />
        <BarChart title="Cost by Stage" bars={data.cost} barColor="bg-success-solid" />
      </Card>
    </CollapsibleSection>
  );
}
