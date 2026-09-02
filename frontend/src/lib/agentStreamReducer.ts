import type { AgentMessage, AgentStreamEvent, HarnessAgentResponse, ManualReviewCase, ManualReviewResolutionResponse, PlanGroupExecutionEvent, PlanNodeExecutionEvent } from "./api";

export type AgentStreamStatus = "idle" | "streaming" | "done" | "error";
export type ToolExecutionStatus = "running" | "done" | "error";

export interface DisplayAgentMessage {
  id: string;
  role: "user" | "assistant" | "toolResult" | "compactionSummary" | "branchSummary" | "custom";
  text: string;
  thinking?: string;
  toolCalls?: Record<string, { id: string; name: string; arguments: Record<string, unknown> }>;
  raw?: AgentMessage | Record<string, unknown>;
}

export interface StreamingAssistant {
  id: string;
  text: string;
  thinking: string;
  toolCalls: Record<string, { id: string; name: string; arguments: Record<string, unknown> }>;
  raw?: AgentMessage | Record<string, unknown>;
}

export interface ToolExecutionState {
  id: string;
  toolName: string;
  args?: unknown;
  status: ToolExecutionStatus;
  summary?: string;
  result?: unknown;
  isError?: boolean;
}

export interface PlanExecutionState {
  planId: string;
  status: string;
  effectiveStatus?: string;
  recovery?: Record<string, unknown>;
  completionReason?: string;
  groups: Record<string, PlanGroupExecutionEvent>;
  nodes: Record<string, PlanNodeExecutionEvent>;
}

export interface AgentStreamState {
  messages: DisplayAgentMessage[];
  streaming: StreamingAssistant | null;
  toolExecutions: Record<string, ToolExecutionState>;
  proposals: Record<string, Record<string, unknown>>;
  plans: Record<string, PlanExecutionState>;
  manualReviewCases: Record<string, ManualReviewCase>;
  cards: unknown[];
  status: AgentStreamStatus;
  error: string;
  finalResponse: HarnessAgentResponse | null;
}

let syntheticId = 0;
export const BUSY_CONTINUATION_MESSAGE = "当前会话正在处理，已确认的结果会在稍后自动跟进。";

export function createInitialAgentStreamState(): AgentStreamState {
  return {
    messages: [],
    streaming: null,
    toolExecutions: {},
    proposals: {},
    plans: {},
    manualReviewCases: {},
    cards: [],
    status: "idle",
    error: "",
    finalResponse: null,
  };
}

export function agentStreamReducer(state: AgentStreamState, event: AgentStreamEvent): AgentStreamState {
  switch (event.type) {
    case "agent_start":
    case "turn_start":
      return { ...state, status: "streaming", error: "" };
    case "message_start":
      return {
        ...state,
        status: "streaming",
        streaming: streamingFromMessage(event.message),
      };
    case "message_update":
      return applyMessageUpdate(state, event);
    case "message_end":
      return solidifyMessage(state, event.message);
    case "tool_execution_start":
      return {
        ...state,
        toolExecutions: {
          ...state.toolExecutions,
          [event.tool_call_id || event.toolCallId || nextId("tool")]: {
            id: event.tool_call_id || event.toolCallId || nextId("tool"),
            toolName: event.tool_name || event.toolName || "tool",
            args: event.args,
            status: "running",
          },
        },
      };
    case "tool_execution_update":
      return updateTool(state, event.tool_call_id || event.toolCallId, {
        summary: textFromUnknown(event.partial_result) || undefined,
        result: event.partial_result,
      });
    case "tool_execution_end":
      return updateTool(state, event.tool_call_id || event.toolCallId, {
        toolName: event.tool_name || event.toolName,
        status: event.is_error ? "error" : "done",
        isError: Boolean(event.is_error),
        result: event.result,
        summary: textFromUnknown(event.result) || undefined,
      });
    case "proposal":
      return mergeProposal(state, proposalFromEvent(event));
    case "plan_status":
      return mergePlanStatus(state, event);
    case "card":
      return { ...state, cards: [...state.cards, event.card ?? event] };
    case "compaction":
      return {
        ...state,
        messages: [
          ...state.messages,
          {
            id: nextId("compaction"),
            role: "custom",
            text: "上下文已压缩：仅保留摘要与最近消息",
            raw: event,
          },
        ],
      };
    case "turn_end":
      return state;
    case "agent_end":
      return { ...state, status: state.status === "error" ? "error" : "done" };
    case "final":
      return {
        ...mergeFinalPayload(state, event),
        finalResponse: event,
        status: state.status === "error" ? "error" : "done",
      };
    case "error":
      return {
        ...state,
        status: "error",
        error: eventErrorText(event),
      };
    default:
      return state;
  }
}



export function markConfirmRequestProcessed<
  T extends { confirmRequest?: { args?: Record<string, unknown>; processed?: boolean } }
>(messages: T[], proposalId: string): T[] {
  const target = String(proposalId || "");
  if (!target) return messages;
  return messages.map((message) => {
    const request = message.confirmRequest;
    if (!request || request.processed || String(request.args?.proposal_id || "") !== target) return message;
    return { ...message, confirmRequest: { ...request, processed: true } };
  });
}


export interface ProposalDecisionUiTransition {
  resolvedProposalIds: Set<string>;
  confirmationChallenges: Record<string, string>;
}

export function proposalDecisionUiTransition(
  resolvedProposalIds: Set<string>,
  confirmationChallenges: Record<string, string>,
  proposalId: string,
  response: { status?: string; confirmation_challenge?: unknown } | null | undefined
): ProposalDecisionUiTransition {
  const nextResolved = new Set(resolvedProposalIds);
  const nextChallenges = { ...confirmationChallenges };
  const status = String(response?.status || "");
  if (status === "awaiting_next_confirmation") {
    nextResolved.delete(proposalId);
    const challenge = String(response?.confirmation_challenge || "");
    if (challenge) nextChallenges[proposalId] = challenge;
  } else if (["confirmed", "rejected", "expired", "conflict", "failed", "manual_review"].includes(status)) {
    nextResolved.add(proposalId);
    delete nextChallenges[proposalId];
  }
  return { resolvedProposalIds: nextResolved, confirmationChallenges: nextChallenges };
}

export function mergeManualReviewCases(
  state: AgentStreamState,
  cases: ManualReviewCase[] | null | undefined
): AgentStreamState {
  if (!cases?.length) return state;
  const manualReviewCases = { ...state.manualReviewCases };
  for (const reviewCase of cases) {
    const caseId = String(reviewCase?.case_id || "");
    if (!caseId) continue;
    manualReviewCases[caseId] = {
      ...(manualReviewCases[caseId] || {}),
      ...reviewCase,
      case_id: caseId,
      status: String(reviewCase.status || manualReviewCases[caseId]?.status || "open"),
    };
  }
  return { ...state, manualReviewCases };
}

export function applyManualReviewResolutionResponse(
  state: AgentStreamState,
  response: ManualReviewResolutionResponse | null | undefined
): AgentStreamState {
  if (!response) return state;
  const returnedCase = response.case || response.manual_review_case;
  let next = returnedCase ? mergeManualReviewCases(state, [returnedCase]) : state;
  const caseId = String(response.case_id || returnedCase?.case_id || "");
  const retainedCase = caseId ? next.manualReviewCases[caseId] : undefined;
  if (caseId && retainedCase) {
    next = mergeManualReviewCases(next, [{
      ...retainedCase,
      case_id: caseId,
      status: String(response.status || (returnedCase ? retainedCase.status || "open" : "resolved")),
      resolution: response.resolution || retainedCase.resolution,
    }]);
  }
  if (response.plan_event) next = agentStreamReducer(next, response.plan_event);
  for (const proposal of response.next_proposals || []) {
    next = mergeProposal(next, proposal);
  }
  return next;
}

export function applyProposalDecisionResponse(
  state: AgentStreamState,
  response: { continuation?: HarnessAgentResponse; next_proposals?: Record<string, unknown>[]; plan_event?: AgentStreamEvent } | null | undefined
): AgentStreamState {
  if (!response) return state;
  let next = applyConfirmContinuation(state, response.continuation);
  for (const proposal of response.next_proposals || []) next = mergeProposal(next, proposal);
  if (response.plan_event) next = agentStreamReducer(next, response.plan_event);
  return next;
}

export function applyConfirmContinuation(
  state: AgentStreamState,
  continuation: HarnessAgentResponse | null | undefined
): AgentStreamState {
  if (!continuation) return state;
  let next: AgentStreamState = { ...state, finalResponse: continuation };
  if (continuation.assistant_message) {
    next = {
      ...next,
      messages: [
        ...next.messages,
        {
          id: nextId("assistant"),
          role: "assistant",
          text: continuation.assistant_message,
          raw: { role: "assistant", content: [{ type: "text", text: continuation.assistant_message }] },
        },
      ],
    };
  }
  for (const proposal of continuation.proposals || []) {
    next = mergeProposal(next, proposal as Record<string, unknown>);
  }
  if (continuation.cards?.length) {
    next = { ...next, cards: [...next.cards, ...continuation.cards] };
  }
  if (continuation.ok === false) {
    const errorText = continuation.error ? textFromUnknown(continuation.error) : "session_busy";
    const isBusy = errorText.includes("session_busy") || errorText.includes("busy");
    next = {
      ...next,
      status: "error",
      error: isBusy ? BUSY_CONTINUATION_MESSAGE : errorText,
      messages: [
        ...next.messages,
        {
          id: nextId("assistant"),
          role: "assistant",
          text: isBusy ? BUSY_CONTINUATION_MESSAGE : errorText,
          raw: { role: "assistant", content: [{ type: "text", text: isBusy ? BUSY_CONTINUATION_MESSAGE : errorText }] },
        },
      ],
    };
  }
  return next;
}

export function displayTextFromAgentMessage(message: AgentMessage | null | undefined): string {
  if (!message) return "";
  if (message.role === "compactionSummary" || message.role === "branchSummary") return message.summary || "";
  if (message.role === "custom") return typeof message.content === "string" ? message.content : textFromBlocks(message.content);
  if (message.role === "toolResult") return textFromBlocks(message.content);
  return textFromBlocks(message.content);
}

export function thinkingFromAgentMessage(message: AgentMessage | null | undefined): string {
  if (!message || !("content" in message) || !Array.isArray(message.content)) return "";
  return message.content
    .map((block) => (block && block.type === "thinking" ? block.thinking || "" : ""))
    .filter(Boolean)
    .join("");
}

function applyMessageUpdate(state: AgentStreamState, event: AgentStreamEvent): AgentStreamState {
  const current = state.streaming || streamingFromMessage(event.message);
  const delta = "delta" in event && event.delta && typeof event.delta === "object" ? event.delta : {};
  const text = stringValue(delta, "text") || stringValue(delta, "content");
  const thinking = stringValue(delta, "thinking");
  const toolCall = toolCallFromDelta(delta);
  const toolCalls = { ...current.toolCalls };
  if (toolCall) toolCalls[toolCall.id] = toolCall;
  return {
    ...state,
    status: "streaming",
    streaming: {
      ...current,
      text: current.text + text,
      thinking: current.thinking + thinking,
      toolCalls,
      raw: event.message || current.raw,
    },
  };
}

function solidifyMessage(state: AgentStreamState, message: AgentMessage | undefined): AgentStreamState {
  const display = displayFromMessageOrStreaming(message, state.streaming);
  if (!display) return { ...state, streaming: null };
  if (isDuplicateUserEcho(state.messages, display)) {
    return { ...state, streaming: null };
  }
  return {
    ...state,
    streaming: null,
    messages: [...state.messages, display],
  };
}

function isDuplicateUserEcho(messages: DisplayAgentMessage[], display: DisplayAgentMessage): boolean {
  if (display.role !== "user") return false;
  const last = messages[messages.length - 1];
  return Boolean(last && last.role === "user" && last.text === display.text);
}

function streamingFromMessage(message: AgentMessage | undefined): StreamingAssistant {
  return {
    id: nextId("streaming"),
    text: displayTextFromAgentMessage(message),
    thinking: thinkingFromAgentMessage(message),
    toolCalls: toolCallsFromMessage(message),
    raw: message,
  };
}

function displayFromMessageOrStreaming(
  message: AgentMessage | undefined,
  streaming: StreamingAssistant | null
): DisplayAgentMessage | null {
  if (!message && !streaming) return null;
  const role = message?.role || "assistant";
  return {
    id: nextId(role),
    role,
    text: displayTextFromAgentMessage(message) || streaming?.text || "",
    thinking: thinkingFromAgentMessage(message) || streaming?.thinking || "",
    toolCalls: toolCallsFromMessage(message) || streaming?.toolCalls || {},
    raw: message || streaming?.raw,
  };
}

function updateTool(
  state: AgentStreamState,
  id: string | undefined,
  patch: Partial<ToolExecutionState>
): AgentStreamState {
  const toolId = id || nextId("tool");
  const previous = state.toolExecutions[toolId] || {
    id: toolId,
    toolName: patch.toolName || "tool",
    status: "running" as ToolExecutionStatus,
  };
  return {
    ...state,
    toolExecutions: {
      ...state.toolExecutions,
      [toolId]: { ...previous, ...patch, id: toolId, toolName: patch.toolName || previous.toolName },
    },
  };
}

function mergeProposal(state: AgentStreamState, proposal: Record<string, unknown> | null): AgentStreamState {
  if (!proposal) return state;
  const proposalId = String(proposal.proposal_id || proposal.id || "");
  if (!proposalId) return state;
  const next = {
    ...state,
    proposals: { ...state.proposals, [proposalId]: { ...(state.proposals[proposalId] || {}), ...proposal } },
  };
  const planId = String(proposal.plan_id || "");
  if (!planId) return next;
  const groupId = String(proposal.confirmation_group_id || "");
  const previous: PlanExecutionState = state.plans[planId] || { planId, status: String(proposal.plan_status || "sealed"), groups: {}, nodes: {} };
  const groups: Record<string, PlanGroupExecutionEvent> = { ...previous.groups };
  if (groupId) {
    groups[groupId] = {
      ...(groups[groupId] || {}),
      group_id: groupId,
      status: String(proposal.group_status || "pending"),
      group_digest: String(proposal.group_digest || "") || undefined,
    };
  }
  const nodes: Record<string, PlanNodeExecutionEvent> = { ...previous.nodes };
  for (const nodeId of Array.isArray(proposal.node_ids) ? proposal.node_ids : []) nodes[String(nodeId)] = { ...(nodes[String(nodeId)] || {}), node_id: String(nodeId), status: "pending" };
  return { ...next, plans: { ...state.plans, [planId]: { ...previous, status: String(proposal.plan_status || previous.status), groups, nodes } } };
}

function mergePlanStatus(state: AgentStreamState, event: AgentStreamEvent): AgentStreamState {
  const planId = String((event as Record<string, unknown>).plan_id || "");
  if (!planId) return state;
  const raw = event as Record<string, unknown>;
  const previous: PlanExecutionState = state.plans[planId] || { planId, status: "unknown", groups: {}, nodes: {} };
  const groups: Record<string, PlanGroupExecutionEvent> = { ...previous.groups };
  for (const group of Array.isArray(raw.groups) ? raw.groups : []) if (group && typeof group === "object") { const value = group as Record<string, unknown>; const id = String(value.group_id || ""); if (id) groups[id] = { ...(groups[id] || {}), ...value, group_id: id, status: String(value.status || groups[id]?.status || "unknown") } as PlanGroupExecutionEvent; }
  const nodes: Record<string, PlanNodeExecutionEvent> = { ...previous.nodes };
  for (const node of Array.isArray(raw.nodes) ? raw.nodes : []) if (node && typeof node === "object") { const value = node as Record<string, unknown>; const id = String(value.node_id || ""); if (id) nodes[id] = { ...(nodes[id] || {}), ...value, node_id: id, status: String(value.status || nodes[id]?.status || "unknown") } as PlanNodeExecutionEvent; }
  const manualReviewCases = { ...state.manualReviewCases };
  for (const node of Array.isArray(raw.nodes) ? raw.nodes : []) {
    if (!node || typeof node !== "object") continue;
    const value = node as Record<string, unknown>;
    const caseId = String(value.manual_review_case_id || "");
    if (!caseId) continue;
    const existing = manualReviewCases[caseId];
    manualReviewCases[caseId] = {
      ...(existing || {}),
      case_id: caseId,
      session_id: String(value.session_id || existing?.session_id || "") || undefined,
      plan_id: planId,
      group_id: String(value.confirmation_group_id || existing?.group_id || "") || undefined,
      node_id: String(value.node_id || existing?.node_id || "") || undefined,
      reason_code: String(value.reason_code || existing?.reason_code || "manual_review_required"),
      effect_state: String(value.effect_state || existing?.effect_state || "unknown_external"),
      evidence: value.evidence ?? existing?.evidence,
      status: String(existing?.status || "open"),
      case_generation: existing?.case_generation,
      evidence_digest: existing?.evidence_digest,
    };
  }
  return {
    ...state,
    plans: {
      ...state.plans,
      [planId]: {
        ...previous,
        status: String(raw.status || previous.status),
        effectiveStatus: String(raw.effective_status || previous.effectiveStatus || "") || undefined,
        recovery: raw.recovery && typeof raw.recovery === "object" ? raw.recovery as Record<string, unknown> : previous.recovery,
        completionReason: String(raw.completion_reason || previous.completionReason || "") || undefined,
        groups,
        nodes,
      },
    },
    manualReviewCases,
  };
}

function mergeFinalPayload(state: AgentStreamState, response: HarnessAgentResponse): AgentStreamState {
  let next = state;
  for (const proposal of response.proposals || []) {
    next = mergeProposal(next, proposal as Record<string, unknown>);
  }
  if (response.cards?.length) {
    next = { ...next, cards: [...next.cards, ...response.cards] };
  }
  return next;
}

function proposalFromEvent(event: AgentStreamEvent): Record<string, unknown> | null {
  if (event.type !== "proposal") return null;
  if (event.proposal && typeof event.proposal === "object") {
    return {
      proposal_id: event.proposal_id,
      risk: event.risk,
      summary: event.summary,
      affected_records: event.affected_records,
      ...(event.proposal as Record<string, unknown>),
    };
  }
  return {
    proposal_id: event.proposal_id,
    risk: event.risk,
    summary: event.summary,
    affected_records: event.affected_records,
  };
}

function toolCallsFromMessage(message: AgentMessage | undefined): Record<string, { id: string; name: string; arguments: Record<string, unknown> }> {
  if (!message || !("content" in message) || !Array.isArray(message.content)) return {};
  const calls: Record<string, { id: string; name: string; arguments: Record<string, unknown> }> = {};
  for (const block of message.content) {
    if (block?.type === "toolCall") {
      calls[block.id] = { id: block.id, name: block.name, arguments: block.arguments || {} };
    }
  }
  return calls;
}

function toolCallFromDelta(delta: Record<string, unknown>): { id: string; name: string; arguments: Record<string, unknown> } | null {
  const raw = delta.toolCall || delta.tool_call || delta.tool_call_delta;
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Record<string, unknown>;
  const id = String(value.id || value.tool_call_id || "");
  if (!id) return null;
  return {
    id,
    name: String(value.name || value.tool_name || ""),
    arguments: typeof value.arguments === "object" && value.arguments ? value.arguments as Record<string, unknown> : {},
  };
}

function textFromBlocks(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (!block || typeof block !== "object") return "";
      const value = block as Record<string, unknown>;
      if (value.type === "text") return String(value.text || "");
      if (value.type === "thinking") return "";
      return "";
    })
    .join("");
}

function textFromUnknown(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "object") {
    const maybe = value as Record<string, unknown>;
    if (Array.isArray(maybe.content)) return textFromBlocks(maybe.content);
    if (typeof maybe.message === "string") return maybe.message;
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function eventErrorText(event: AgentStreamEvent): string {
  if (event.type !== "error") return "";
  return textFromUnknown(event.error || event.message || "Agent stream failed");
}

function stringValue(delta: Record<string, unknown>, key: string): string {
  const value = delta[key];
  return typeof value === "string" ? value : "";
}

function nextId(prefix: string): string {
  syntheticId += 1;
  return `${prefix}_${syntheticId}`;
}
