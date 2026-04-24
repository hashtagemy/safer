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
import saferLogo from "@/assets/safer.svg";

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
    <aside className="w-60 shrink-0 border-r border-border bg-card/70 flex flex-col backdrop-blur-sm">
      <div className="flex flex-col items-center justify-center gap-2 px-3 py-4 border-b border-border">
        <img
          src={saferLogo}
          alt="SAFER"
          className="w-[65%] h-auto drop-shadow-[0_0_3px_rgba(125,211,252,0.15)]"
        />
        <span className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">
          Agent Control Plane
        </span>
      </div>
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                "group relative flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-all duration-150",
                isActive
                  ? "bg-safer-ice/12 text-safer-ice shadow-[inset_2px_0_0_0_rgba(125,211,252,0.85)]"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              )
            }
          >
            {({ isActive }) => (
              <>
                <item.icon
                  className={cn(
                    "h-4 w-4 transition-colors",
                    isActive
                      ? "text-safer-ice"
                      : "text-muted-foreground group-hover:text-safer-ice/90"
                  )}
                />
                {item.label}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-border px-4 py-3 text-xs text-muted-foreground font-mono flex items-center justify-between">
        <span>v0.1.0</span>
        <span className="h-1.5 w-1.5 rounded-full bg-safer-ice animate-pulse" />
      </div>
    </aside>
  );
}
