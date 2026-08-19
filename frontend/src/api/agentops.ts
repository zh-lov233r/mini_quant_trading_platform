import type {
  AgentApproval,
  AgentToolRun,
  AgentWorkflow,
  AgentWorkflowRun,
} from "@/types/agentops";

export const AGENTOPS_API_BASE = (
  process.env.NEXT_PUBLIC_AGENTOPS_API_BASE_URL || "http://localhost:8100"
).replace(/\/$/, "");

export const AGENTOPS_PROJECT_ID = process.env.NEXT_PUBLIC_AGENTOPS_PROJECT_ID || "";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${AGENTOPS_API_BASE}${path}`, {
    credentials: "include",
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
  });
  if (!response.ok) {
    const raw = await response.text();
    let message = raw;
    try {
      const payload = JSON.parse(raw) as { detail?: unknown };
      if (typeof payload.detail === "string") message = payload.detail;
      else if (payload.detail && typeof payload.detail === "object" && "message" in payload.detail) {
        message = String(payload.detail.message);
      }
    } catch {
      // Preserve non-JSON responses.
    }
    throw new Error(message || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function listAgentWorkflows(): Promise<AgentWorkflow[]> {
  if (!AGENTOPS_PROJECT_ID) throw new Error("NEXT_PUBLIC_AGENTOPS_PROJECT_ID is not configured.");
  const result = await request<{ items: AgentWorkflow[] }>(
    `/v1/projects/${encodeURIComponent(AGENTOPS_PROJECT_ID)}/workflows`,
  );
  return result.items;
}

export async function startAgentWorkflow(
  workflowName: string,
  goal: string,
  inputs: Record<string, unknown> = {},
): Promise<AgentWorkflowRun> {
  const workflows = await listAgentWorkflows();
  const workflow = workflows.find((item) => item.name === workflowName && item.currentVersionId);
  if (!workflow) throw new Error(`Published AgentOps workflow not found: ${workflowName}`);
  const created = await request<AgentWorkflowRun>(`/v1/workflows/${workflow.id}/runs`, {
    method: "POST",
    body: JSON.stringify({ workflowVersionId: workflow.currentVersionId, inputs: { goal, ...inputs } }),
  });
  return request<AgentWorkflowRun>(`/v1/workflow-runs/${created.id}/start`, { method: "POST" });
}

export function getAgentWorkflowRun(id: string): Promise<AgentWorkflowRun> {
  return request<AgentWorkflowRun>(`/v1/workflow-runs/${encodeURIComponent(id)}`);
}

export async function listAgentApprovals(id: string): Promise<AgentApproval[]> {
  const result = await request<{ items: AgentApproval[] }>(
    `/v1/workflow-runs/${encodeURIComponent(id)}/approval-requests`,
  );
  return result.items;
}

export async function listAgentToolRuns(id: string): Promise<AgentToolRun[]> {
  const result = await request<{ items: AgentToolRun[] }>(
    `/v1/workflow-runs/${encodeURIComponent(id)}/tool-runs`,
  );
  return result.items;
}

export function approveAgentRequest(id: string, note?: string): Promise<AgentApproval> {
  return request<AgentApproval>(`/v1/approval-requests/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify({ note: note || null }),
  });
}

export function rejectAgentRequest(id: string, note?: string): Promise<AgentApproval> {
  return request<AgentApproval>(`/v1/approval-requests/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ note: note || null }),
  });
}

export function cancelAgentWorkflow(id: string): Promise<AgentWorkflowRun> {
  return request<AgentWorkflowRun>(`/v1/workflow-runs/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
}

export async function retryAgentWorkflow(run: AgentWorkflowRun): Promise<AgentWorkflowRun> {
  const created = await request<AgentWorkflowRun>(
    `/v1/workflows/${encodeURIComponent(run.workflowDefinitionId)}/runs`,
    {
      method: "POST",
      body: JSON.stringify({ workflowVersionId: run.workflowVersionId, inputs: run.inputs }),
    },
  );
  return request<AgentWorkflowRun>(`/v1/workflow-runs/${created.id}/start`, { method: "POST" });
}
