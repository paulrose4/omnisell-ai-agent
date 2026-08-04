export type TaskStatus = "pending" | "running" | "succeeded" | "failed" | "dead_letter" | "closed";

export interface AgentTask {
  task_id: string;
  queue_name: string;
  action: string;
  status: TaskStatus;
  created_at: number;
  updated_at: number;
  started_at?: number | null;
  finished_at?: number | null;
  request_id?: string | null;
  result?: unknown;
  error?: string | null;
  latency_ms?: number | null;
  attempts?: number;
  max_attempts?: number;
  replayed_from_task_id?: string | null;
  replayed_to_task_id?: string | null;
  closed_by?: string | null;
  close_reason?: string | null;
}

export interface TaskListResponse {
  total: number;
  items: AgentTask[];
}

export interface ReconciliationDifference {
  id: string;
  resource_type: string;
  resource_id: string;
  difference_type: string;
  amazon_value: unknown;
  internal_value: unknown;
  created_at: string;
}

export interface ReconciliationRun {
  id: string;
  connection_id: string;
  status: "matched" | "requires_manual_review" | string;
  summary: Record<string, unknown>;
  started_at: string;
  completed_at?: string | null;
  data_source: "amazon_api" | "mock" | "unknown" | string;
  verified_amazon_data: boolean;
  amazon_observed_at?: string | null;
  amazon_collected_at?: string | null;
  freshness_status: "fresh" | "stale" | "unknown" | string;
  differences: ReconciliationDifference[];
}

export interface ReconciliationRunListResponse {
  total: number;
  items: ReconciliationRun[];
}

export type AgentRunStatus = "received" | "validated" | "evidence_collected" | "analyzed" | "output_validated" | "planned" | "policy_evaluated" | "awaiting_approval" | "approved" | "executing" | "verifying" | "succeeded" | "degraded" | "failed" | "canceled";
export type WorkflowClass = "deterministic_analysis" | "bounded_advisory" | "read_only_investigation" | "approval_gated_action";

export interface AgentRun {
  run_id: string;
  tenant_id: string;
  actor_id: string;
  workflow_type: string;
  workflow_name: string;
  workflow_class: WorkflowClass;
  generation_method: "deterministic" | "model";
  amazon_write_capability: boolean;
  agent_type?: string | null;
  correlation_id: string;
  status: AgentRunStatus;
  prompt_version: string;
  model?: string | null;
  model_usage?: Record<string, unknown>;
  evidence_manifest?: Record<string, unknown>;
  analysis_result?: Record<string, unknown>;
  error_category?: string | null;
  retry_count?: number;
  dispatch_task_id?: string | null;
  dispatch_task_status?: string | null;
  token_usage: number;
  estimated_cost: string;
  state: {
    history?: Array<{ status: AgentRunStatus; at: string }>;
    request?: Record<string, unknown>;
    plan?: Record<string, unknown>;
    approvals?: Array<Record<string, unknown>>;
    results?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
  created_at: string;
  updated_at: string;
}

export interface AgentRunListResponse {
  total: number;
  items: AgentRun[];
}

export type RiskLevel = "low" | "medium" | "high" | "critical";
export type ApprovalStatus = "pending" | "awaiting_second_approval" | "approved" | "rejected" | "expired" | "auto_approved";

export interface ApprovalRequest {
  request_id: string;
  action: string;
  risk_level: RiskLevel;
  payload: Record<string, unknown> & {
    action_hash?: string;
    plan?: unknown;
    evidence?: unknown;
    before_snapshot?: unknown;
    proposed_changes?: unknown;
    resource_id?: string;
  };
  requester: string;
  status: ApprovalStatus;
  reason: string;
  requires_second_verification: boolean;
  created_at: string;
  expires_at: string;
  decided_at?: string | null;
  decided_by?: string | null;
  decision_reason?: string | null;
  first_approver_id?: string | null;
  first_approved_at?: string | null;
  first_approval_reason?: string | null;
  second_approver_id?: string | null;
  second_approved_at?: string | null;
  second_approval_reason?: string | null;
}

export interface ApprovalListResponse {
  total: number;
  items: ApprovalRequest[];
}

export interface ApprovalDecisionInput {
  reason: string;
}

export interface ApiErrorPayload {
  error?: string;
  message?: string;
  correlation_id?: string | null;
}

export type WorkItemStatus = "open" | "assigned" | "in_progress" | "blocked" | "awaiting_approval" | "verifying" | "completed" | "canceled";

export interface OperationalWorkItem {
  id: string;
  tenant_id: string;
  source_type: string;
  source_id: string;
  operational_issue_id: string;
  recommended_action_id?: string | null;
  domain: string;
  team: string;
  seller_sku: string;
  marketplace_id: string;
  brand?: string | null;
  assignee_id?: string | null;
  severity: IssueSeverity;
  priority_score: string;
  title: string;
  description: string;
  status: WorkItemStatus;
  due_at: string;
  acknowledged_at?: string | null;
  sla_policy_version: string;
  escalation_level: number;
  expected_outcome: string;
  baseline_metrics: Record<string, unknown>;
  result_metrics: Record<string, unknown>;
  verification_status: string;
  verification_attempts: number;
  evidence_manifest: Record<string, unknown>[];
  action_hash: string;
  handoff_note?: string | null;
  blocked_reason?: string | null;
  last_handoff_at?: string | null;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface WorkItemListResponse {
  total: number;
  page: number;
  page_size: number;
  pages: number;
  has_next: boolean;
  items: OperationalWorkItem[];
  generated_at: string;
}

export interface WorkloadSummary {
  total_active: number;
  unassigned: number;
  overdue: number;
  assignees: Array<{
    assignee_id: string;
    total: number;
    critical: number;
    overdue: number;
    awaiting_approval: number;
    blocked: number;
  }>;
}

export type IssueSeverity = "low" | "medium" | "high" | "critical";
export type IssueStatus = "open" | "acknowledged" | "in_progress" | "awaiting_approval" | "approved_for_manual_execution" | "verifying" | "verification_failed" | "resolved" | "dismissed" | "reopened";

export interface OperationalIssue {
  id: string;
  seller_sku: string;
  marketplace_id: string;
  issue_type: string;
  severity: IssueSeverity;
  title: string;
  summary: string;
  status: IssueStatus;
  confidence: string;
  currency?: string | null;
  estimated_revenue_impact?: string | null;
  estimated_profit_impact?: string | null;
  estimated_cost_impact?: string | null;
  degraded_reason?: string | null;
  missing_data: string[];
  last_detected_at: string;
  assignee_id?: string | null;
  due_at?: string | null;
  acknowledged_at?: string | null;
  started_at?: string | null;
  resolved_at?: string | null;
  resolution_code?: string | null;
  execution_result_id?: string | null;
  reopened_count?: number;
  priority_score?: string;
  evidence?: Record<string, unknown>[];
  data_freshness_at: string;
}

export interface RecommendedAction {
  id: string;
  issue_id: string;
  action_type: string;
  title: string;
  description: string;
  execution_mode: string;
  risk_level: IssueSeverity;
  status: string;
  expected_outcome: string;
  automation_eligible: boolean;
  parameters: Record<string, unknown>;
  evidence: Record<string, unknown>[];
  confidence: string;
  currency?: string | null;
  expected_revenue_impact?: string | null;
  expected_profit_impact?: string | null;
  expected_cost_impact?: string | null;
  rejection_reason?: string | null;
}

export interface OperationalIssueDetailResponse {
  issue: OperationalIssue;
  recommended_actions: RecommendedAction[];
  pending_approval?: ApprovalRequest | null;
  amazon_write_performed: false;
  seller_central_required: true;
}

export interface OperationalIssueApprovalResponse {
  created: boolean;
  issue: OperationalIssue;
  recommended_action: RecommendedAction;
  approval_request: ApprovalRequest;
}

export interface ApprovedManualExecutionPlan {
  execution_contract_version: "manual-execution-v1";
  connection_id: string;
  issue_id: string;
  recommended_action_id: string;
  recommended_action_type: "review_replenishment";
  action_type: "create_replenishment_plan";
  changes: { inbound_quantity: number };
}

export interface ApprovedManualExecution {
  approval_request_id: string;
  action_hash: string;
  execution_contract_version: string;
  execution_plan: ApprovedManualExecutionPlan;
  expires_at: string;
  issue: Pick<OperationalIssue, "id" | "seller_sku" | "marketplace_id" | "title" | "summary" | "severity" | "status" | "data_freshness_at">;
  recommended_action: Pick<RecommendedAction, "id" | "action_type" | "title" | "description" | "risk_level" | "status" | "expected_outcome">;
  approvals: {
    requester: string;
    first_approver_id?: string | null;
    first_approved_at?: string | null;
    second_approver_id?: string | null;
    second_approved_at?: string | null;
  };
}

export interface ApprovedManualExecutionListResponse {
  total: number;
  items: ApprovedManualExecution[];
}

export interface SKUHealthSummary {
  tenant_id: string;
  seller_sku: string;
  marketplace_id: string;
  health_status: IssueSeverity;
  highest_severity: IssueSeverity;
  open_issue_count: number;
  issue_types: string[];
  recommended_action_ids: string[];
  impact_by_currency: Record<string, { revenue: string; profit: string; cost: string }>;
  has_data_quality_risk: boolean;
  last_detected_at: string;
}

export interface DailyOperationsBrief {
  id: string;
  brief_date: string;
  timezone: string;
  generated_at: string;
  issue_counts: Record<IssueSeverity, number>;
  domain_counts: Record<string, number>;
  total_open_issues: number;
  data_quality_issue_count: number;
  impact_by_currency: Record<string, { revenue: string; profit: string; cost: string }>;
  unallocated_impact_issue_count: number;
  priority_issue_ids: string[];
  priority_action_ids: string[];
  sku_health: SKUHealthSummary[];
}

export interface DashboardPriority {
  issue: OperationalIssue;
  action?: RecommendedAction | null;
}

export interface OperationsDashboardResponse {
  data_mode: "mock" | "connected";
  data_disclaimer?: string | null;
  queue_summary: {
    urgent: number;
    overdue: number;
    awaiting_approval: number;
    stale_data: number;
    verification_failed: number;
    my_tasks: number;
    shift_owner: string;
    open_customer_cases: number;
    actionable_case_clusters: number;
    unlinked_actionable_case_clusters: number;
    case_flow_inconsistencies: number;
  };
  brief: DailyOperationsBrief;
  priorities: DashboardPriority[];
  sku_health: SKUHealthSummary[];
}

export interface SKUHealthResponse {
  items: SKUHealthSummary[];
  total: number;
  brief_id: string;
}

export type ListingQualityStatus = "critical" | "poor" | "fair" | "good";
export type ListingExperimentStatus = "awaiting_approval" | "approved" | "observing" | "completed" | "directional_improvement" | "confounded" | "inconclusive" | "rollback_recommended" | "kept" | "rollback_planned";
export type ListingExecutionStatus = "not_executed" | "executed" | "verified";

export interface ListingQualitySnapshot {
  id: string;
  tenant_id: string;
  listing_id: string;
  seller_sku: string;
  marketplace_id: string;
  content_version: number;
  title: string;
  bullet_points: string[];
  description: string;
  image_count: number;
  search_terms: string[];
  listing_status: string;
  quality_score: string;
  quality_status: ListingQualityStatus;
  section_scores: Record<string, string>;
  quality_issues: Array<{ section: string; code: string; severity: string; rationale: string }>;
  recommendations: Array<{ section: string; action: string; rationale: string; risk_level: string; execution_mode: string }>;
  evidence: Record<string, unknown>[];
  confidence: string;
  missing_data: string[];
  degraded_reason?: string | null;
  automation_eligible: boolean;
  source: string;
  source_observed_at?: string | null;
  calculated_at: string;
  calculation_version: string;
  baseline_metrics: Record<string, string | number | null>;
}

export interface ListingExperiment {
  id: string;
  tenant_id: string;
  listing_id: string;
  seller_sku: string;
  marketplace_id: string;
  before_snapshot_id: string;
  after_snapshot_id?: string | null;
  proposed_changes: Record<string, unknown>;
  baseline_metrics: Record<string, string | number | null>;
  observation_start: string;
  observation_end: string;
  expected_outcome: string;
  action_hash: string;
  status: ListingExperimentStatus;
  approval_request_id?: string | null;
  effect_metrics: Record<string, string>;
  evaluation_reasons: string[];
  rollback_recommended: boolean;
  evaluated_at?: string | null;
  execution_status: ListingExecutionStatus;
  actual_changes: Record<string, unknown>;
  execution_evidence: Record<string, unknown>;
  executed_by?: string | null;
  executed_at?: string | null;
  execution_notes?: string | null;
  decision?: "keep" | "rollback" | null;
  decision_by?: string | null;
  decision_at?: string | null;
  decision_reason?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ListingSnapshotListResponse { total: number; items: ListingQualitySnapshot[] }
export interface ListingExperimentListResponse { total: number; items: ListingExperiment[] }
export interface ListingExperimentMutationResponse {
  experiment: ListingExperiment;
  platform_write_executed: false;
  replayed?: boolean;
  approval_request?: ApprovalRequest;
}

export type CustomerCaseStatus = "open" | "acknowledged" | "in_progress" | "awaiting_approval" | "ready_to_send" | "policy_blocked" | "sent" | "resolved" | "dismissed" | "reopened";

export interface CustomerCaseCluster {
  id: string;
  status: "collecting" | "linked" | "resolved";
  marketplace_id: string;
  seller_sku: string;
  root_cause_code: string;
  window_start: string;
  window_end: string;
  minimum_samples: number;
  sample_count: number;
  severity: IssueSeverity;
  domain: string;
  team: string;
  operational_issue_id?: string | null;
  recommended_action_id?: string | null;
  work_item_id?: string | null;
  evidence_manifest: Record<string, unknown>[];
  verified_amazon_data: boolean;
}

export interface CustomerCaseOperationsContext {
  status: string;
  cluster: CustomerCaseCluster;
  work_item?: Pick<OperationalWorkItem, "id" | "source_type" | "source_id" | "operational_issue_id" | "domain" | "team" | "seller_sku" | "marketplace_id" | "severity" | "title" | "status" | "baseline_metrics" | "result_metrics" | "verification_status"> | null;
  related_cases: Array<{ case_id: string; status: CustomerCaseStatus; severity: IssueSeverity; created_at: string }>;
}

export interface WorkItemCustomerCaseContext {
  cluster: CustomerCaseCluster | null;
  case_ids: string[];
  related_case_count: number;
}

export interface CustomerCase {
  id: string;
  external_case_id: string;
  marketplace_id: string;
  external_order_id?: string | null;
  seller_sku?: string | null;
  language: string;
  intent: string;
  sentiment: string;
  urgency: string;
  severity: IssueSeverity;
  risk_level: RiskLevel;
  priority_score: string;
  customer_message: string;
  status: CustomerCaseStatus;
  assignee_id?: string | null;
  due_at?: string | null;
  draft_reply?: string | null;
  approved_reply?: string | null;
  actual_reply?: string | null;
  confidence: string;
  evidence: Record<string, unknown>;
  missing_data: string[];
  policy_checks: { decision?: string; reason?: string; violations?: string[]; risk_level?: string };
  playbook_key?: string | null;
  playbook_version?: string | null;
  approval_request_id?: string | null;
  resolution_code?: string | null;
  root_cause_code?: string | null;
  source: string;
  verified_amazon_data: boolean;
  created_at: string;
  updated_at: string;
  timeline?: Array<{ event_type: string; actor_id: string; payload: Record<string, unknown>; created_at: string }>;
  operations_context?: CustomerCaseOperationsContext | null;
}

export interface CustomerCaseListResponse {
  total: number;
  page: number;
  page_size: number;
  pages: number;
  has_next: boolean;
  items: CustomerCase[];
}
