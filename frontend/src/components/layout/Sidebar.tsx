import { NavLink } from "react-router-dom";
import { X, LogOut } from "lucide-react";
import { NAV_SECTIONS } from "./nav-items";
import { Logomark } from "./Logomark";
import { useAuth } from "../../app/auth-context";

interface SidebarProps {
  open: boolean;
  onClose: () => void;
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const { user, logout } = useAuth();

  return (
    <>
      {/* Mobile backdrop — clicking it closes the drawer. Desktop never renders it. */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-slate-950/70 md:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-slate-800 bg-slate-900 transition-transform duration-200 ease-in-out md:static md:z-auto md:translate-x-0 ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between px-5 py-5">
          <div className="flex items-center gap-2.5">
            <Logomark className="h-8 w-8 shrink-0" />
            <span className="font-display text-base font-bold tracking-tight text-slate-50">
              GraphForge
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-100 md:hidden"
            aria-label="Close navigation"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <nav className="flex-1 space-y-1 px-3">
          {NAV_SECTIONS.map((group) => (
            <div key={group.section ?? "root"} className="pb-1">
              {group.section && (
                <p className="px-3 pt-4 pb-1 text-[11px] font-semibold tracking-wide text-slate-600 uppercase">
                  {group.section}
                </p>
              )}
              {group.items.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  end={item.path === "/"}
                  onClick={onClose}
                  className={({ isActive }) =>
                    `relative flex items-center gap-3 rounded-lg py-2 pl-3 pr-3 text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-brand-500/10 text-brand-200 before:absolute before:inset-y-1.5 before:left-0 before:w-0.5 before:rounded-full before:bg-brand-400"
                        : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                    }`
                  }
                >
                  <item.icon className="h-4 w-4" aria-hidden="true" />
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="border-t border-slate-800 px-5 py-4">
          <p className="text-xs text-slate-500">Sample data — no repositories connected yet.</p>
          {user && (
            <div className="mt-3 flex items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-200">{user.full_name}</p>
                <p className="truncate text-xs text-slate-500">{user.email}</p>
              </div>
              <button
                type="button"
                onClick={logout}
                title="Log out"
                className="shrink-0 rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-100"
              >
                <LogOut className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
