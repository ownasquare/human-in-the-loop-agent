import { Info } from "lucide-react";
import { useId } from "react";

interface InfoTooltipProps {
  label: string;
  children: string;
}

export function InfoTooltip({ label, children }: InfoTooltipProps) {
  const tooltipId = useId();

  return (
    <span className="info-tooltip">
      <button
        className="info-tooltip__trigger"
        type="button"
        aria-label={label}
        aria-describedby={tooltipId}
      >
        <Info aria-hidden="true" />
      </button>
      <span className="info-tooltip__content" id={tooltipId} role="tooltip">
        {children}
      </span>
    </span>
  );
}
