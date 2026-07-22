import type { LucideIcon } from "lucide-react";
import { Card } from "./Card";

interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  icon?: LucideIcon;
}

/** A single KPI tile, built on Card — used in a grid on the Dashboard. */
export function StatCard({ label, value, hint, icon: Icon }: StatCardProps) {
  return (
    <Card className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</p>
        {Icon && <Icon className="h-4 w-4 text-sky-400" aria-hidden="true" />}
      </div>
      <p className="text-2xl font-semibold text-slate-50">{value}</p>
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </Card>
  );
}
