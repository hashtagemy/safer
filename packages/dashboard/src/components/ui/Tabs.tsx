import {
  createContext,
  useContext,
  useMemo,
  type HTMLAttributes,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

interface TabsContextValue {
  value: string;
  onChange: (value: string) => void;
}

const TabsContext = createContext<TabsContextValue | null>(null);

function useTabsContext(source: string): TabsContextValue {
  const ctx = useContext(TabsContext);
  if (!ctx) {
    throw new Error(`<${source}> must be rendered inside <Tabs>`);
  }
  return ctx;
}

export interface TabsProps {
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
  className?: string;
}

export function Tabs({ value, onChange, children, className }: TabsProps) {
  const ctx = useMemo(() => ({ value, onChange }), [value, onChange]);
  return (
    <TabsContext.Provider value={ctx}>
      <div className={cn("flex flex-col gap-3", className)}>{children}</div>
    </TabsContext.Provider>
  );
}

export function TabsList({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-border bg-card/40 p-1 text-xs font-mono",
        className
      )}
      {...props}
    />
  );
}

export interface TabsTriggerProps extends HTMLAttributes<HTMLButtonElement> {
  value: string;
  disabled?: boolean;
}

export function TabsTrigger({
  value,
  disabled,
  className,
  children,
  ...props
}: TabsTriggerProps) {
  const { value: active, onChange } = useTabsContext("TabsTrigger");
  const isActive = active === value;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={isActive}
      aria-controls={`tabs-content-${value}`}
      disabled={disabled}
      onClick={() => !disabled && onChange(value)}
      className={cn(
        "inline-flex items-center gap-1.5 rounded px-3 py-1.5 transition",
        "disabled:opacity-40 disabled:cursor-not-allowed",
        isActive
          ? "bg-muted text-foreground border border-safer-ice/40 shadow-[0_0_0_1px_rgba(96,165,250,0.15)_inset]"
          : "text-muted-foreground hover:text-foreground hover:bg-muted/40 border border-transparent",
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

export interface TabsContentProps extends HTMLAttributes<HTMLDivElement> {
  value: string;
  /** If true, keep the content mounted but hidden when inactive. */
  mount?: "active" | "always";
}

export function TabsContent({
  value,
  mount = "active",
  className,
  children,
  ...props
}: TabsContentProps) {
  const { value: active } = useTabsContext("TabsContent");
  const isActive = active === value;
  if (mount === "active" && !isActive) return null;
  return (
    <div
      role="tabpanel"
      id={`tabs-content-${value}`}
      hidden={!isActive}
      className={cn(!isActive && "hidden", className)}
      {...props}
    >
      {children}
    </div>
  );
}
