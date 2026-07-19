import { prettyKey, printableValue } from "../utils";

export function JsonDetails({ value, label = "Technical details" }: { value: Record<string, unknown>; label?: string }) {
  const entries = Object.entries(value);
  if (!entries.length) return <p className="muted">No additional fields were provided.</p>;
  return (
    <details className="technical-details">
      <summary>{label}</summary>
      <dl className="detail-list">
        {entries.map(([key, item]) => (
          <div key={key}>
            <dt>{prettyKey(key)}</dt>
            <dd><pre>{printableValue(item)}</pre></dd>
          </div>
        ))}
      </dl>
    </details>
  );
}
