export interface AgentWorkflow {
  id: string;
  name: string;
  status: string;
  currentVersionId: string | null;
}

export interface AgentWorkflowRun {
  id: string;
  projectId: string;
  workflowDefinitionId: string;
  workflowVersionId: string;
  status: string;
  currentNodeId: string | null;
  inputs: Record<string, unknown>;
  resultSummary: Record<string, unknown>;
  lastError: string | null;
  workflowName: string | null;
  createdAt: string;
  updatedAt: string;
  finishedAt: string | null;
  nodeRuns?: AgentNodeRun[];
}

export interface AgentNodeRun {
  id: string;
  nodeId: string;
  nodeType: string;
  status: string;
  output: Record<string, unknown> | null;
  error: string | null;
}

export interface AgentApproval {
  id: string;
  action: string;
  status: string;
  reason: string;
  payload: Record<string, unknown>;
  resolutionNote: string | null;
  createdAt: string;
}

export interface AgentToolRun {
  id: string;
  toolId: string;
  status: string;
  externalRef: string | null;
  response: Record<string, unknown>;
  outputSummary: string | null;
  errorCode: string | null;
  errorMessage: string | null;
  attempts: number;
}
