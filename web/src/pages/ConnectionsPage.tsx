import { CalendarDays, CheckCircle2, Database, Mail, Search, Settings2, ShoppingCart } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { RetryButton, StatePanel } from "../components/StatePanel";
import { StatusBadge } from "../components/StatusBadge";
import { useConnectors } from "../queries";
import { formatDate, prettyKey } from "../utils";

const CONNECTOR_ICONS: Record<string, LucideIcon> = {
  search: Search,
  web_search: Search,
  email: Mail,
  calendar: CalendarDays,
  database: Database,
  records: Database,
  purchasing: ShoppingCart,
};

export function ConnectionsPage() {
  const connectors = useConnectors();
  const items = connectors.data?.items ?? [];

  return (
    <div className="page-stack">
      <section className="page-intro">
        <p className="eyebrow">Workspace tools</p>
        <h2>See what Relay can use.</h2>
        <p>
          Demo tools stay local. Live setup status never displays credentials or secret values.
        </p>
      </section>
      <section className="section-block" aria-labelledby="connections-title">
        <div className="section-heading">
          <div><p className="eyebrow">Available tools</p><h2 id="connections-title">Connections</h2></div>
        </div>
        {connectors.isLoading ? (
          <StatePanel kind="loading" title="Checking connections" description="Reading sanitized connector readiness." />
        ) : connectors.isError ? (
          <StatePanel kind="error" title="Connection status is unavailable" description="Relay did not expose any secret values. Try the readiness check again." action={<RetryButton onClick={() => void connectors.refetch()} />} />
        ) : items.length ? (
          <div className="connection-grid">
            {items.map((connector) => {
              const Icon = CONNECTOR_ICONS[connector.category ?? connector.id] ?? Settings2;
              return (
                <article className="connection-card" key={connector.id}>
                  <header>
                    <span className="connection-card__icon" aria-hidden="true"><Icon /></span>
                    <div><h3>{connector.name}</h3><span>{connector.provider || prettyKey(connector.id)}</span></div>
                    <StatusBadge status={connector.status} label={prettyKey(connector.status)} />
                  </header>
                  <p>{connector.description || connector.detail || "Connector readiness is reported by the Relay service."}</p>
                  {connector.capabilities?.length ? (
                    <ul className="capability-list">
                      {connector.capabilities.map((capability) => <li key={capability}><CheckCircle2 aria-hidden="true" />{prettyKey(capability)}</li>)}
                    </ul>
                  ) : null}
                  <footer>
                    <span>Mode: {prettyKey(connector.mode || "local")}</span>
                    {connector.last_checked_at ? <span>Checked {formatDate(connector.last_checked_at)}</span> : null}
                  </footer>
                </article>
              );
            })}
          </div>
        ) : (
          <StatePanel kind="empty" title="No connectors reported" description="Run the Relay readiness check, then refresh this page." />
        )}
      </section>
      <aside className="info-card">
        <Settings2 aria-hidden="true" />
        <div>
          <h2>Live services are optional</h2>
          <p>
            Demo mode uses local records and receipts. Enabling a live service is an operator task and does not weaken approval policy.
          </p>
        </div>
      </aside>
    </div>
  );
}
