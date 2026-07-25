import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  label: string;
  value: string | number;
  icon: LucideIcon;
  color?: "brand" | "emerald" | "amber" | "rose" | "slate";
  href?: string;
  subtext?: ReactNode;
}

const colorMap = {
  brand: "text-brand-400 bg-brand-500/10 ring-brand-500/20",
  emerald: "text-emerald-400 bg-emerald-500/10 ring-emerald-500/20",
  amber: "text-amber-400 bg-amber-500/10 ring-amber-500/20",
  rose: "text-rose-400 bg-rose-500/10 ring-rose-500/20",
  slate: "text-slate-400 bg-slate-800/60 ring-slate-700/40",
};

export function StatCard({ label, value, icon: Icon, color = "slate", subtext }: StatCardProps) {
  const colors = colorMap[color];

  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-800/60 bg-slate-900/40 px-4 py-3">
      <div className={`rounded-lg p-2 ring-1 ring-inset ${colors}`}>
        <Icon className="h-4 w-4" aria-hidden="true" />
      </div>
      <div className="min-w-0">
        <p className="text-lg font-semibold tabular-nums text-slate-100">{value}</p>
        <p className="truncate text-xs text-slate-500">{subtext ?? label}</p>
      </div>
    </div>
  );
}
