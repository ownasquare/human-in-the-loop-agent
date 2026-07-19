import { Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { AuditPage } from "./pages/AuditPage";
import { ConnectionsPage } from "./pages/ConnectionsPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { RunPage } from "./pages/RunPage";
import { WorkPage } from "./pages/WorkPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<WorkPage />} />
        <Route path="approvals" element={<ApprovalsPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="connections" element={<ConnectionsPage />} />
        <Route path="runs/:runId" element={<RunPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
