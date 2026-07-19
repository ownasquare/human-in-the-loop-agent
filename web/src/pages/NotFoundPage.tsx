import { ArrowLeft } from "lucide-react";
import { Link } from "react-router-dom";
import { StatePanel } from "../components/StatePanel";

export function NotFoundPage() {
  return (
    <StatePanel
      kind="empty"
      title="Page not found"
      description="This Relay workspace page does not exist."
      action={<Link className="button button--primary" to="/"><ArrowLeft aria-hidden="true" />Return to work</Link>}
    />
  );
}
