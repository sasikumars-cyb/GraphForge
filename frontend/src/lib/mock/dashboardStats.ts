/** Sample data only — no backend is connected yet. */
export interface DashboardStat {
  label: string;
  value: string;
  hint: string;
}

export const mockDashboardStats: DashboardStat[] = [
  { label: "Repositories monitored", value: "5", hint: "across 1 organization" },
  { label: "Open pull requests", value: "4", hint: "2 awaiting analysis" },
  { label: "High risk changes", value: "3", hint: "critical or high this week" },
  { label: "Avg. analysis time", value: "38s", hint: "per pull request" },
];
