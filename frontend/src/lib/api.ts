// =============================================
// API 客户端 — 统一的后端请求封装
// =============================================
// 所有前端组件通过此模块与后端通信
// 基于 fetch API，支持 SWR 缓存
// =============================================

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://127.0.0.1:8000");

function buildQuery(params?: Record<string, unknown>) {
  const sp = new URLSearchParams();
  if (!params) return sp.toString();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    if (typeof value === "string" && value.trim() === "") continue;
    sp.set(key, String(value));
  }
  return sp.toString();
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    throw new Error(`无法连接本地后端 ${API_BASE}，请确认后端服务已启动。原始错误：${reason}`);
  }
  if (!res.ok) {
    let detail = "";
    try {
      const payload: unknown = await res.json();
      const raw = (payload as { detail?: unknown } | null)?.detail ?? payload;
      detail = typeof raw === "string" ? raw : JSON.stringify(raw ?? "");
    } catch {
      detail = "";
    }
    throw new Error(`API Error: ${res.status}${detail ? ` - ${detail}` : ""}`);
  }
  return res.json();
}

// ---- Jobs API ----
export const jobsApi = {
  list: (params?: {
    page?: number;
    page_size?: number;
    period?: string;
    source?: string;
    triage_status?: "inbox" | "picked" | "ignored";
    pool_id?: number | "ungrouped";
    batch_id?: string;
    keyword?: string;
    job_type?: string;
    education?: string;
    is_campus?: boolean;
  }) =>
    request(`/api/jobs/?${buildQuery(params as any)}`),
  
  get: (id: number) => request(`/api/jobs/${id}`),

  batches: (limit = 30) => request(`/api/jobs/batches?limit=${limit}`),

  patch: (
    id: number,
    data: { triage_status?: "inbox" | "picked" | "ignored"; user_notes?: string; pool_id?: number; clear_pool?: boolean }
  ) =>
    request(`/api/jobs/${id}`, { method: "PATCH", body: JSON.stringify(data) }),

  patchBatch: (data: {
    job_ids: number[];
    triage_status?: "inbox" | "picked" | "ignored";
    pool_id?: number;
    clear_pool?: boolean;
  }) =>
    request("/api/jobs/batch-update", { method: "PATCH", body: JSON.stringify(data) }),
  
  stats: (period = "week") => request(`/api/jobs/stats?period=${period}`),
};

// ---- Pools API ----
export const poolsApi = {
  list: (scope?: "inbox" | "picked" | "ignored") =>
    request(`/api/pools/?${buildQuery({ scope })}`),

  create: (data: { name: string; scope?: "inbox" | "picked" | "ignored" }) =>
    request("/api/pools/", { method: "POST", body: JSON.stringify(data) }),

  update: (id: number, data: { name: string }, scope?: "inbox" | "picked" | "ignored") =>
    request(`/api/pools/${id}?${buildQuery({ scope })}`, { method: "PUT", body: JSON.stringify(data) }),

  delete: (id: number, scope?: "inbox" | "picked" | "ignored") =>
    request(`/api/pools/${id}?${buildQuery({ scope })}`, { method: "DELETE" }),
};

// ---- Resume API ----
export const resumeApi = {
  list: () => request("/api/resume/"),

  get: (id: number) => request(`/api/resume/${id}`),

  create: (data: any) =>
    request("/api/resume/", { method: "POST", body: JSON.stringify(data) }),

  update: (id: number, data: any) =>
    request(`/api/resume/${id}`, { method: "PUT", body: JSON.stringify(data) }),

  delete: (id: number) =>
    request(`/api/resume/${id}`, { method: "DELETE" }),

  // 段落管理
  createSection: (resumeId: number, data: any) =>
    request(`/api/resume/${resumeId}/sections`, { method: "POST", body: JSON.stringify(data) }),

  updateSection: (resumeId: number, sectionId: number, data: any) =>
    request(`/api/resume/${resumeId}/sections/${sectionId}`, { method: "PUT", body: JSON.stringify(data) }),

  deleteSection: (resumeId: number, sectionId: number) =>
    request(`/api/resume/${resumeId}/sections/${sectionId}`, { method: "DELETE" }),

  reorderSections: (resumeId: number, items: { id: number; sort_order: number }[]) =>
    request(`/api/resume/${resumeId}/sections/reorder`, { method: "PUT", body: JSON.stringify({ items }) }),

  // 文件上传
  uploadPhoto: async (resumeId: number, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/api/resume/${resumeId}/photo`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(`API Error: ${res.status}`);
    return res.json();
  },

  // 导出
  exportPdf: (id: number) =>
    fetch(`${API_BASE}/api/resume/${id}/export/pdf`, { method: "POST" }),

  // 模板
  templates: () => request("/api/resume/templates"),
};

// ---- Calendar API ----
export const calendarApi = {
  events: (start?: string, end?: string) => {
    const params = new URLSearchParams();
    if (start) params.set("start", start);
    if (end) params.set("end", end);
    return request(`/api/calendar/events?${params}`);
  },
  
  createEvent: (data: any) =>
    request("/api/calendar/events", { method: "POST", body: JSON.stringify(data) }),
  
  autoFill: () =>
    request("/api/calendar/auto-fill", { method: "POST" }),
};

// ---- Email API ----
export const emailApi = {
  auth: () => request("/api/email/auth", { method: "POST" }),
  
  notifications: () => request("/api/email/notifications"),
  
  sync: () => request("/api/email/sync", { method: "POST" }),
};

// ---- Config API ----
export const configApi = {
  get: () => request("/api/config/"),
  
  update: (data: any) =>
    request("/api/config/", { method: "PUT", body: JSON.stringify(data) }),
};

// ---- Harness Agent API ----
export interface HarnessAgentMessage {
  role: "user" | "assistant";
  content: string;
}

export type AgentContentBlock =
  | { type: "text"; text: string; details?: unknown }
  | { type: "thinking"; thinking: string; details?: unknown }
  | { type: "toolCall"; id: string; name: string; arguments: Record<string, unknown>; details?: unknown }
  | { type: "image"; url: string; mime_type?: string; details?: unknown };

export type AgentMessage =
  | { role: "user"; content: AgentContentBlock[]; timestamp?: number; details?: unknown }
  | {
      role: "assistant";
      content: AgentContentBlock[];
      stop_reason?: string | null;
      usage?: Record<string, unknown> | null;
      model?: string;
      provider?: string;
      error_message?: string | null;
      timestamp?: number;
      details?: unknown;
    }
  | {
      role: "toolResult";
      tool_call_id: string;
      tool_name: string;
      content: AgentContentBlock[];
      is_error?: boolean;
      timestamp?: number;
      details?: unknown;
    }
  | { role: "compactionSummary"; summary: string; tokens_before?: number; timestamp?: number; details?: unknown }
  | { role: "branchSummary"; summary: string; from_id?: string; timestamp?: number; details?: unknown }
  | {
      role: "custom";
      custom_type?: string;
      content: string | AgentContentBlock[];
      display?: boolean;
      timestamp?: number;
      details?: unknown;
    };

export interface HarnessAgentToolCall {
  tool: string;
  args: Record<string, unknown>;
  result: unknown;
  action_id?: string;
}

export interface HarnessAgentProposedAction {
  id: string;
  tool: string;
  summary: string;
  risk_level: "read" | "write" | "confirm";
  requires_confirmation: boolean;
  args: Record<string, unknown>;
}

export interface HarnessAgentCareerPath {
  title: string;
  industry: string;
  fit_reason: string;
  entry_route: string;
  salary_range: string;
  search_keywords: string[];
  application_strategy: string;
}

export interface HarnessAgentJobCard {
  id: number;
  title: string;
  company: string;
  location: string;
  salary_text: string;
  source: string;
  apply_url: string;
  summary?: string;
}

export interface HarnessAgentAlert {
  code: string;
  severity: "low" | "medium" | "high" | string;
  title: string;
  message: string;
  action?: string;
}

export interface HarnessAgentProactiveSuggestion {
  title: string;
  description: string;
  prompt: string;
}

export interface HarnessAgentMemorySnapshot {
  schema_version: string;
  user_stage: "unknown" | "campus" | "experienced" | string;
  confidence: number;
  facts: string[];
  preferences: string[];
  goals: string[];
  risks: string[];
  events: string[];
  updated_at: string;
}

export interface HarnessAgentConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message: string;
}

export interface HarnessAgentConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: HarnessAgentMessage[];
}

export interface HarnessAgentSessionBootstrap {
  proposals: Record<string, unknown>[];
  plan_events: AgentStreamEvent[];
}

export interface HarnessAgentResponse {
  ok?: boolean;
  conversation_id?: string;
  assistant_message: string;
  proposals?: Record<string, unknown>[];
  cards?: unknown[];
  stop_reason?: string;
  incomplete_turn?: boolean;
  incomplete_assistant_message?: string;
  error?: unknown;
  mode?: string;
  requires_confirmation?: boolean;
  tool_calls?: HarnessAgentToolCall[];
  proposed_actions?: HarnessAgentProposedAction[];
  career_paths?: HarnessAgentCareerPath[];
  job_cards?: HarnessAgentJobCard[];
  next_steps?: string[];
  transferable_skills_summary?: string;
  quick_wins?: string[];
  reality_check?: Record<string, any>;
  user_stage?: "unknown" | "campus" | "experienced" | string;
  stage_confidence?: number;
  stage_signals?: string[];
  memory_snapshot?: HarnessAgentMemorySnapshot;
  alerts?: HarnessAgentAlert[];
  proactive_suggestions?: HarnessAgentProactiveSuggestion[];
  conversation_title?: string;
}

export interface HarnessAgentChatRequest {
  messages: HarnessAgentMessage[];
  memory?: Record<string, any>;
  conversation_id?: string | null;
  page_context?: Record<string, any> | string | null;
}

export type AgentEventType =
  | "agent_start"
  | "turn_start"
  | "message_start"
  | "message_update"
  | "message_end"
  | "tool_execution_start"
  | "tool_execution_update"
  | "tool_execution_end"
  | "turn_end"
  | "agent_end"
  | "proposal"
  | "plan_status"
  | "card"
  | "compaction"
  | "final"
  | "error";

export interface PlanGroupExecutionEvent {
  [key: string]: unknown;
  group_id: string;
  status: string;
  group_digest?: string;
  result_receipt_id?: string;
  result_digest?: string;
  confirmations_required?: number;
  confirmations_received?: number;
  authorized_at?: string;
}

export interface PlanNodeExecutionEvent {
  [key: string]: unknown;
  node_id: string;
  status: string;
  confirmation_group_id?: string;
  atomic_group_id?: string;
  receipt_status?: string;
  write_occurred?: boolean;
  completion_reason?: string;
  execution_started_at?: string;
  outcome_id?: string;
  execution_contract_digest?: string;
  effect_manifest_digest?: string;
  effect_state?: "committed" | "no_effect" | "rolled_back" | "unknown_external" | "legacy_unproven" | string;
  manual_review_case_id?: string;
}

export type AgentStreamEvent =
  | ({ type: "agent_start" | "turn_start" | "turn_end" | "agent_end" } & Record<string, any>)
  | ({ type: "message_start" | "message_end"; message?: AgentMessage } & Record<string, any>)
  | ({ type: "message_update"; message?: AgentMessage; delta?: Record<string, any> } & Record<string, any>)
  | ({
      type: "tool_execution_start" | "tool_execution_update" | "tool_execution_end";
      tool_call_id?: string;
      toolCallId?: string;
      tool_name?: string;
      toolName?: string;
      args?: Record<string, unknown>;
      partial_result?: unknown;
      result?: unknown;
      is_error?: boolean;
    } & Record<string, any>)
  | ({
      type: "proposal";
      proposal_id?: string;
      risk?: unknown;
      summary?: string;
      affected_records?: unknown[];
      proposal?: Record<string, unknown>;
    } & Record<string, any>)
  | ({ type: "plan_status"; plan_id?: string; status?: string; groups?: PlanGroupExecutionEvent[]; nodes?: PlanNodeExecutionEvent[]; completion_reason?: string } & Record<string, any>)
  | ({ type: "card"; card?: unknown } & Record<string, any>)
  | ({ type: "compaction" } & Record<string, any>)
  | ({ type: "final" } & HarnessAgentResponse & Record<string, any>)
  | ({ type: "error"; error?: unknown; message?: string } & Record<string, any>);

export type AgentStreamHandlers = Partial<{
  [K in AgentEventType]: (event: Extract<AgentStreamEvent, { type: K }>) => void;
}> & {
  onEvent?: (event: AgentStreamEvent) => void;
  onError?: (error: Error) => void;
  onFinal?: (event: Extract<AgentStreamEvent, { type: "final" }>) => void;
};

export interface AgentTreeEntry {
  entry_id: string;
  parent_id?: string | null;
  entry_type: string;
  created_at: string;
  preview: string;
}

export interface AgentTreeMessage {
  entry_id?: string | null;
  role: AgentMessage["role"] | "user" | "assistant" | string;
  content: string;
  message?: AgentMessage | Record<string, unknown>;
}

export interface AgentConversationTree {
  entries: AgentTreeEntry[];
  leaf_id?: string | null;
  messages?: AgentTreeMessage[];
}

export interface AgentTreeNavigateResponse {
  ok: boolean;
  conversation_id?: string;
  leaf_id?: string;
  warning?: unknown;
  error?: unknown;
}

export interface ProposalDecisionResponse {
  ok?: boolean;
  proposal_id?: string;
  status?: string;
  continuation?: HarnessAgentResponse;
  next_proposals?: Record<string, unknown>[];
  resolved_proposal_ids?: string[];
  plan_event?: AgentStreamEvent;
  plan_status?: string;
  confirmation_challenge?: string;
  confirmations_required?: number;
  confirmations_received?: number;
  remaining?: number;
  [key: string]: unknown;
}

export type ManualReviewResolution =
  | "effect_absent_retry"
  | "effect_present_accept"
  | "compensation_completed"
  | "abort_plan";

export interface ManualReviewCase {
  [key: string]: unknown;
  case_id: string;
  session_id?: string;
  plan_id?: string;
  group_id?: string;
  node_id?: string;
  proposal_id?: string;
  status?: string;
  reason_code?: string;
  reason?: unknown;
  subject_type?: string;
  effect_state?: string;
  effect?: unknown;
  evidence?: unknown;
  evidence_json?: unknown;
  case_generation?: number;
  evidence_digest?: string;
  resolution?: ManualReviewResolution | string;
}

export interface ManualReviewResolveRequest {
  session_id: string;
  resolution: ManualReviewResolution;
  case_generation: number;
  evidence_digest: string;
  idempotency_key: string;
  evidence: Record<string, unknown>;
}

export interface ManualReviewResolutionResponse {
  [key: string]: unknown;
  case_id?: string;
  status?: string;
  resolution?: ManualReviewResolution | string;
  case?: ManualReviewCase;
  manual_review_case?: ManualReviewCase;
  plan_event?: AgentStreamEvent;
  retry_plan_id?: string;
  recovery_plan_id?: string;
  recovery?: Record<string, unknown>;
  next_proposals?: Record<string, unknown>[];
}

const AGENT_EVENT_TYPES = new Set<AgentEventType>([
  "agent_start",
  "turn_start",
  "message_start",
  "message_update",
  "message_end",
  "tool_execution_start",
  "tool_execution_update",
  "tool_execution_end",
  "turn_end",
  "agent_end",
  "proposal",
  "plan_status",
  "card",
  "compaction",
  "final",
  "error",
]);

function isAgentEventType(value: string): value is AgentEventType {
  return AGENT_EVENT_TYPES.has(value as AgentEventType);
}

function findSseBoundary(text: string) {
  const unix = text.indexOf("\n\n");
  const windows = text.indexOf("\r\n\r\n");
  if (unix === -1) return windows;
  if (windows === -1) return unix;
  return Math.min(unix, windows);
}

function parseSseBlock(block: string): { eventType: string; dataText: string } | null {
  let eventType = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim() || "message";
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  return { eventType, dataText: dataLines.join("\n") };
}

function dispatchAgentEvent(event: AgentStreamEvent, handlers: AgentStreamHandlers) {
  handlers.onEvent?.(event);
  if (event.type === "final") handlers.onFinal?.(event);
  const handler = handlers[event.type] as ((event: AgentStreamEvent) => void) | undefined;
  handler?.(event);
}

async function streamAgentEndpoint(
  path: string,
  body: Record<string, unknown>,
  handlers: AgentStreamHandlers = {},
  signal?: AbortSignal
) {
  let finalSeen = false;
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok || !res.body) {
      const errBody = await res.json().catch(() => null);
      throw new Error(errBody?.detail?.error || errBody?.detail || `Agent stream failed (${res.status})`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    const emitBlock = (block: string) => {
      const parsedBlock = parseSseBlock(block);
      if (!parsedBlock) return;
      let parsed: any;
      try {
        parsed = JSON.parse(parsedBlock.dataText);
      } catch (error) {
        handlers.onError?.(new Error(`Agent stream JSON parse failed: ${String(error)}`));
        return;
      }
      const type = String(parsed.type || parsed.event || parsedBlock.eventType || "");
      if (!isAgentEventType(type)) return;
      const event = { ...parsed, type } as AgentStreamEvent;
      if (event.type === "final") finalSeen = true;
      dispatchAgentEvent(event, handlers);
    };

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = findSseBoundary(buffer);
      while (boundary >= 0) {
        const separatorLength = buffer.slice(boundary, boundary + 4) === "\r\n\r\n" ? 4 : 2;
        const block = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + separatorLength);
        if (block) emitBlock(block);
        boundary = findSseBoundary(buffer);
      }
    }
    const tail = buffer.trim();
    if (tail) emitBlock(tail);
  } catch (error) {
    if (signal?.aborted) return;
    const err = error instanceof Error ? error : new Error(String(error));
    handlers.onError?.(err);
    if (!finalSeen) throw err;
  }
}

export function agentChatStream(
  data: HarnessAgentChatRequest,
  handlers: AgentStreamHandlers = {},
  signal?: AbortSignal
) {
  return streamAgentEndpoint("/api/harness-agent/chat/stream", data as unknown as Record<string, unknown>, handlers, signal);
}

export function optimizeAgentChatStream(
  data: { session_id: string; message: string; action?: string; feedback?: string },
  handlers: AgentStreamHandlers = {},
  signal?: AbortSignal
) {
  return streamAgentEndpoint("/api/optimize/agent/chat/stream", data as Record<string, unknown>, handlers, signal);
}

function manualReviewCaseFromPayload(payload: ManualReviewCase | { case?: ManualReviewCase; manual_review_case?: ManualReviewCase }): ManualReviewCase {
  const envelope = payload as { case?: ManualReviewCase; manual_review_case?: ManualReviewCase };
  const reviewCase = envelope.case || envelope.manual_review_case || payload as ManualReviewCase;
  if (!reviewCase.case_id) throw new Error("Manual-review case response is missing case_id");
  return reviewCase;
}

export const harnessAgentApi = {
  chat: (data: HarnessAgentChatRequest) =>
    request<HarnessAgentResponse>("/api/harness-agent/chat", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  conversations: () =>
    request<{ conversations: HarnessAgentConversationSummary[] }>("/api/harness-agent/conversations"),
  conversation: (id: string) =>
    request<HarnessAgentConversationDetail>(`/api/harness-agent/conversations/${encodeURIComponent(id)}`),
  sessionBootstrap: (sessionId: string) =>
    request<HarnessAgentSessionBootstrap>(
      `/api/harness-agent/sessions/${encodeURIComponent(sessionId)}/bootstrap`
    ),
  deleteConversation: (id: string) =>
    request<{ ok: boolean }>(`/api/harness-agent/conversations/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  exportMemory: (format: "json" | "markdown" = "json") =>
    request<{ format: string; content: any; memory: HarnessAgentMemorySnapshot }>(
      `/api/harness-agent/memory/export?${buildQuery({ format })}`
    ),
  importMemory: (content: Record<string, any> | string) =>
    request<{ ok: boolean; memory: HarnessAgentMemorySnapshot }>("/api/harness-agent/memory/import", {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  confirmProposal: (proposalId: string, sessionId: string, body?: Record<string, unknown>) =>
    request<ProposalDecisionResponse>(`/api/harness-agent/proposals/${encodeURIComponent(proposalId)}/confirm`, {
      method: "POST",
      body: JSON.stringify({ ...(body || {}), operator_session_id: sessionId }),
    }),
  rejectProposal: (proposalId: string, sessionId: string, body?: Record<string, unknown>) =>
    request<ProposalDecisionResponse>(`/api/harness-agent/proposals/${encodeURIComponent(proposalId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ ...(body || {}), operator_session_id: sessionId }),
    }),
  listManualReviewCases: async (sessionId: string): Promise<ManualReviewCase[]> => {
    const payload = await request<ManualReviewCase[] | { cases?: ManualReviewCase[] }>(
      `/api/harness-agent/manual-review-cases?${buildQuery({ session_id: sessionId })}`
    );
    return Array.isArray(payload) ? payload : payload.cases || [];
  },
  getManualReviewCase: async (caseId: string, sessionId: string): Promise<ManualReviewCase> => {
    const payload = await request<ManualReviewCase | { case?: ManualReviewCase; manual_review_case?: ManualReviewCase }>(
      `/api/harness-agent/manual-review-cases/${encodeURIComponent(caseId)}?${buildQuery({ session_id: sessionId })}`
    );
    return manualReviewCaseFromPayload(payload);
  },
  resolveManualReviewCase: (caseId: string, body: ManualReviewResolveRequest) =>
    request<ManualReviewResolutionResponse>(
      `/api/harness-agent/manual-review-cases/${encodeURIComponent(caseId)}/resolve`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  getConversationTree: (id: string) =>
    request<AgentConversationTree>(`/api/harness-agent/conversations/${encodeURIComponent(id)}/tree`),
  navigateConversationTree: (id: string, entryId: string) =>
    request<AgentTreeNavigateResponse>(`/api/harness-agent/conversations/${encodeURIComponent(id)}/tree/navigate`, {
      method: "POST",
      body: JSON.stringify({ entry_id: entryId }),
    }),
};

export const confirmProposal = harnessAgentApi.confirmProposal;
export const rejectProposal = harnessAgentApi.rejectProposal;
export const getConversationTree = harnessAgentApi.getConversationTree;
export const navigateConversationTree = harnessAgentApi.navigateConversationTree;

// ---- Profile API ----
export interface ProfileAgentPatch {
  action: "ask_user" | "propose_patch" | "apply_patch" | "generate_resume" | "finish";
  assistant_message: string;
  base_info: Record<string, string>;
  target_roles: string[];
  sections: {
    section_type: string;
    category_label?: string;
    title: string;
    content_json: Record<string, any>;
    confidence: number;
  }[];
  next_question?: string;
  confidence?: number;
}

export interface ProfileAgentResponse {
  session_id: number;
  state: Record<string, any>;
  assistant_message: string;
  patch: ProfileAgentPatch;
  agent_trace?: Record<string, any>[];
  stop_reason?: string;
}

export interface ProfileAgentSessionDetail {
  id: number;
  status: string;
  state: Record<string, any>;
  pending_patch?: ProfileAgentPatch | null;
  messages_json: Record<string, any>[];
}

export const profileApi = {
  get: () => request("/api/profile/"),

  update: (data: any) =>
    request("/api/profile/", { method: "PUT", body: JSON.stringify(data) }),

  listTargetRoles: () => request("/api/profile/target-roles"),

  createTargetRole: (data: { role_name: string; role_level?: string; fit?: string }) =>
    request("/api/profile/target-roles", { method: "POST", body: JSON.stringify(data) }),

  // 兼容旧组件调用签名
  addTargetRole: (data: { title: string; fit_level?: string; role_level?: string }) =>
    request("/api/profile/target-roles", {
      method: "POST",
      body: JSON.stringify({
        role_name: data.title,
        role_level: data.role_level,
        fit: data.fit_level || "primary",
      }),
    }),

  deleteTargetRole: (id: number) =>
    request(`/api/profile/target-roles/${id}`, { method: "DELETE" }),

  createSection: (data: any) =>
    request("/api/profile/sections", { method: "POST", body: JSON.stringify(data) }),

  updateSection: (id: number, data: any) =>
    request(`/api/profile/sections/${id}`, { method: "PUT", body: JSON.stringify(data) }),

  deleteSection: (id: number) =>
    request(`/api/profile/sections/${id}`, { method: "DELETE" }),

  chat: async (data: { topic: string; message: string; session_id?: number }) => {
    const res = await fetch(`${API_BASE}/api/profile/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error(`API Error: ${res.status}`);
    return res;
  },

  importResume: async (file: File, parseMode: "ai" | "mechanical" = "ai") => {
    const formData = new FormData();
    formData.append("file", file);
    const params = new URLSearchParams({ parse_mode: parseMode });
    const res = await fetch(`${API_BASE}/api/profile/import-resume?${params.toString()}`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(`API Error: ${res.status}`);
    return res.json();
  },

  listChatSessions: (limit = 20) =>
    request(`/api/profile/chat/sessions?limit=${limit}`),

  getChatSession: (sessionId: number) =>
    request(`/api/profile/chat/sessions/${sessionId}`),

  confirmBullet: (data: { session_id: number; bullet_index: number; edits?: Record<string, any> }) =>
    request("/api/profile/chat/confirm", { method: "POST", body: JSON.stringify(data) }),

  instantDraft: (data: { experiences: string[]; target_roles?: string[] }) =>
    request("/api/profile/instant-draft", { method: "POST", body: JSON.stringify(data) }),

  generateNarrative: () =>
    request("/api/profile/generate-narrative", { method: "POST" }),

  startProfileAgent: async (data: {
    file?: File | null;
    resume_text?: string;
    target_role?: string;
    target_city?: string;
    job_goal?: string;
  }): Promise<ProfileAgentResponse> => {
    const formData = new FormData();
    if (data.file) formData.append("file", data.file);
    formData.append("resume_text", data.resume_text || "");
    formData.append("target_role", data.target_role || "");
    formData.append("target_city", data.target_city || "");
    formData.append("job_goal", data.job_goal || "");

    const res = await fetch(`${API_BASE}/api/profile/agent/start`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(`API Error: ${res.status}`);
    return res.json();
  },

  sendProfileAgentMessage: (data: { session_id: number; message: string }) =>
    request<ProfileAgentResponse>("/api/profile/agent/message", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  getProfileAgentSession: (sessionId: number) =>
    request<ProfileAgentSessionDetail>(`/api/profile/agent/sessions/${sessionId}`),

  applyProfileAgentPatch: (data: { session_id: number; patch?: ProfileAgentPatch }) =>
    request("/api/profile/agent/apply-patch", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};
