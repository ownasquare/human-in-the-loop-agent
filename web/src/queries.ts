import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { relayApi } from "./api";
import type { AuditFilters, CreateRunInput, DecisionInput, RunAggregate } from "./types";

export const queryKeys = {
  runs: ["runs"] as const,
  run: (runId: string) => ["runs", runId] as const,
  approvals: ["approvals"] as const,
  approval: (approvalId: string) => ["approvals", approvalId] as const,
  audit: (filters: AuditFilters) => ["audit-events", filters] as const,
  connectors: ["connectors"] as const,
  capabilities: ["capabilities"] as const,
};

const ACTIVE_RUN_STATES = new Set(["queued", "planning", "running", "awaiting_approval"]);

export function useRuns() {
  return useQuery({
    queryKey: queryKeys.runs,
    queryFn: ({ signal }) => relayApi.listRuns(signal),
    staleTime: 5_000,
    refetchInterval: 10_000,
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: queryKeys.run(runId),
    queryFn: ({ signal }) => relayApi.getRun(runId, signal),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const aggregate = query.state.data;
      return aggregate && ACTIVE_RUN_STATES.has(aggregate.run.status) ? 2_000 : false;
    },
  });
}

export function useApprovals() {
  return useQuery({
    queryKey: queryKeys.approvals,
    queryFn: ({ signal }) => relayApi.listApprovals(signal),
    staleTime: 3_000,
    refetchInterval: 8_000,
  });
}

export function useApproval(approvalId: string | null) {
  return useQuery({
    queryKey: queryKeys.approval(approvalId ?? ""),
    queryFn: ({ signal }) => relayApi.getApproval(approvalId ?? "", signal),
    enabled: Boolean(approvalId),
  });
}

export function useAuditEvents(filters: AuditFilters) {
  return useQuery({
    queryKey: queryKeys.audit(filters),
    queryFn: ({ signal }) => relayApi.listAuditEvents(filters, signal),
    staleTime: 5_000,
  });
}

export function useConnectors() {
  return useQuery({
    queryKey: queryKeys.connectors,
    queryFn: ({ signal }) => relayApi.listConnectors(signal),
    staleTime: 15_000,
  });
}

export function useCapabilities() {
  return useQuery({
    queryKey: queryKeys.capabilities,
    queryFn: ({ signal }) => relayApi.getCapabilities(signal),
    staleTime: 30_000,
  });
}

export function useCreateRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateRunInput) => relayApi.createRun(input),
    onSuccess: async (aggregate) => {
      client.setQueryData<RunAggregate>(queryKeys.run(aggregate.run.id), aggregate);
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.runs }),
        client.invalidateQueries({ queryKey: queryKeys.approvals }),
      ]);
    },
  });
}

export function useDecision(approvalId: string, runId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: DecisionInput) => relayApi.decide(approvalId, input),
    onSuccess: async (aggregate) => {
      client.setQueryData<RunAggregate>(queryKeys.run(aggregate.run.id || runId), aggregate);
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.runs }),
        client.invalidateQueries({ queryKey: queryKeys.approvals }),
        client.invalidateQueries({ queryKey: ["audit-events"] }),
      ]);
    },
  });
}
