import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Activity,
  Bot,
  ListTree,
  Shield,
  BarChart3,
  Swords,
  FileText,
  Settings as SettingsIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/overview", label: "Overview", icon: LayoutDashboard },
  { to: "/live", label: "Live", icon: Activity },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/sessions", label: "Sessions", icon: ListTree },
  { to: "/policies", label: "Policies", icon: Shield },
  { to: "/quality", label: "Quality", icon: BarChart3 },
  { to: "/redteam", label: "Red-Team", icon: Swords },
  { to: "/reports", label: "Reports", icon: FileText },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function Sidebar() {
  return (
    <aside className="w-60 shrink-0 border-r border-border bg-card/50 flex flex-col">
      <div className="h-14 flex items-center gap-2 px-4 border-b border-border">
        <div className="h-8 w-8 rounded-md bg-safer-ice/20 border border-safer-ice/40 flex items-center justify-center">
          <span className="text-safer-ice font-mono font-bold text-sm">S</span>
        </div>
        <div className="flex flex-col leading-tight">
          <span className="font-semibold tracking-tight">SAFER</span>
          <span className="text-xs text-muted-foreground">Agent Control Plane</span>
        </div>
      </div>
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              )
            }
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground font-mono">
        v0.1.0
      </div>
    </aside>
  );
}
