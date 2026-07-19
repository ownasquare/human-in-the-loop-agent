import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Inbox, LoaderCircle, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";

interface StatePanelProps {
  kind: "loading" | "error" | "empty" | "offline";
  title: string;
  description: string;
  action?: ReactNode;
  icon?: LucideIcon;
}

const DEFAULT_ICONS: Record<StatePanelProps["kind"], LucideIcon> = {
  loading: LoaderCircle,
  error: AlertTriangle,
  empty: Inbox,
  offline: WifiOff,
};

export function StatePanel({ kind, title, description, action, icon }: StatePanelProps) {
  const Icon = icon ?? DEFAULT_ICONS[kind];
  return (
    <section className={`state-panel state-panel--${kind}`} aria-busy={kind === "loading"}>
      <span className="state-panel__icon" aria-hidden="true">
        <Icon className={kind === "loading" ? "spin" : undefined} />
      </span>
      <h2>{title}</h2>
      <p>{description}</p>
      {action ? <div className="state-panel__action">{action}</div> : null}
    </section>
  );
}

export function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="button button--secondary" type="button" onClick={onClick}>
      <RefreshCw aria-hidden="true" />
      Try again
    </button>
  );
}
