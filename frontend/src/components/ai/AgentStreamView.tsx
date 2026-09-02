"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Card, CardBody, Chip, Textarea } from "@nextui-org/react";
import { AlertTriangle, CheckCircle2, ChevronDown, FileText, Loader2, Wrench, XCircle } from "lucide-react";
import { harnessAgentApi, type AgentConversationTree, type HarnessAgentMessage, type ManualReviewCase, type ManualReviewResolution, type ManualReviewResolutionResponse } from "@/lib/api";
import type { DisplayAgentMessage, PlanExecutionState, StreamingAssistant, ToolExecutionState } from "@/lib/agentStreamReducer";

export function makeDisplayMessage(
  role: "user" | "assistant",
  text: string,
  id = `${role}-${Date.now()}-${Math.random().toString(36).slice(2)}`
): DisplayAgentMessage {
  return { id, role, text };
}

export function apiMessagesFromDisplay(messages: DisplayAgentMessage[]): HarnessAgentMessage[] {
  return messages
    .filter((message) => (message.role === "user" || message.role === "assistant") && !message.id.startsWith("welcome"))
    .map((message) => ({ role: message.role as "user" | "assistant", content: message.text }));
}

export function messagesFromTree(tree: AgentConversationTree | null | undefined): DisplayAgentMessage[] {
  return (tree?.messages || []).map((message, index) => ({
    id: message.entry_id || `tree-message-${index}`,
    role: normalizeDisplayRole(message.role),
    text: message.content || "",
    raw: message.message,
  }));
}

export function proposalId(proposal: Record<string, unknown>): string {
  return String(proposal.proposal_id || proposal.id || "");
}

export function proposalSummary(proposal: Record<string, unknown>): string {
  return String(proposal.summary || proposal.title || proposal.description || proposalId(proposal) || "待确认 proposal");
}

export function riskLabel(proposal: Record<string, unknown>): string {
  const raw = proposal.risk || proposal.risk_level || proposal.riskLevel;
  return raw === undefined || raw === null || raw === "" ? "需要确认" : `风险 ${String(raw)}`;
}

export function planGroupText(proposal: Record<string, unknown>): string {
  const plan_id = String(proposal.plan_id || "");
  const confirmation_group_id = String(proposal.confirmation_group_id || "");
  const status = String(proposal.group_status || proposal.plan_status || "pending");
  if (!plan_id) return "";
  return `计划 ${plan_id.slice(0, 14)} · 确认组 ${confirmation_group_id.slice(0, 14) || "待创建"} · ${status}`;
}

export function recoveryText(proposal: Record<string, unknown>): string {
  const compensation_policy = String(proposal.compensation_policy || "");
  const status = String(proposal.group_status || proposal.plan_status || "");
  if (status === "partially_completed") return "计划已部分完成；未完成节点将按补偿策略处理。";
  if (status === "manual_review") return "执行结果需要人工复核，系统不会自动重试未知错误。";
  return compensation_policy ? `补偿策略：${compensation_policy}` : "";
}

export function affectedRecordsText(proposal: Record<string, unknown>): string {
  const records = proposal.affected_records || proposal.affectedRecords;
  if (!Array.isArray(records) || records.length === 0) return "";
  return records
    .slice(0, 4)
    .map((record) => {
      if (!record || typeof record !== "object") return String(record);
      const value = record as Record<string, unknown>;
      return String(value.label || value.title || value.id || value.record_id || JSON.stringify(value));
    })
    .join(" / ");
}

export function AgentStreamMessageBubble({
  message,
  compact = false,
}: {
  message: DisplayAgentMessage;
  compact?: boolean;
}) {
  const isUser = message.role === "user";
  const isAssistant = message.role === "assistant";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`${compact ? "max-w-[92%]" : "max-w-[92%] md:max-w-[84%]"} ${isUser ? "text-right" : "text-left"}`}>
        <div
          className={`inline-block max-w-full whitespace-pre-wrap break-words border-2 border-black px-3 py-2 text-sm font-medium leading-6 shadow-[2px_2px_0_0_rgba(18,18,18,0.18)] ${
            isUser ? "bg-[#F7E4E1] text-black" : isAssistant ? "bg-white text-black" : "bg-[var(--surface-muted)] text-black/70"
          }`}
        >
          {message.text || (message.role === "toolResult" ? "工具返回已记录。" : "")}
        </div>
        {message.thinking && (
          <details className="mt-2 border border-black/20 bg-[#f6f4ee] px-3 py-2 text-left text-xs text-black/65">
            <summary className="flex cursor-pointer items-center gap-1 font-bold text-black">
              <ChevronDown size={13} />
              思考过程
            </summary>
            <div className="mt-2 whitespace-pre-wrap break-words leading-5">{message.thinking}</div>
          </details>
        )}
      </div>
    </div>
  );
}

export function StreamingAssistantBubble({ streaming, compact = false }: { streaming: StreamingAssistant; compact?: boolean }) {
  return (
    <div className="flex justify-start">
      <div className={compact ? "max-w-[92%]" : "max-w-[92%] md:max-w-[84%]"}>
        <div className="inline-block max-w-full whitespace-pre-wrap break-words border-2 border-black bg-white px-3 py-2 text-sm font-medium leading-6 text-black shadow-[2px_2px_0_0_rgba(18,18,18,0.18)]">
          {streaming.text || "正在思考..."}
        </div>
        {streaming.thinking && (
          <details className="mt-2 border border-black/20 bg-[#f6f4ee] px-3 py-2 text-xs text-black/65">
            <summary className="flex cursor-pointer items-center gap-1 font-bold text-black">
              <ChevronDown size={13} />
              思考过程
            </summary>
            <div className="mt-2 whitespace-pre-wrap break-words leading-5">{streaming.thinking}</div>
          </details>
        )}
      </div>
    </div>
  );
}

export function ToolExecutionList({ executions }: { executions: Record<string, ToolExecutionState> }) {
  const rows = Object.values(executions);
  if (rows.length === 0) return null;
  return (
    <div className="space-y-2">
      {rows.map((tool) => (
        <div key={tool.id} className="border border-black/20 bg-white px-3 py-2 text-xs text-black/70">
          <div className="flex items-center gap-2 font-bold text-black">
            {tool.status === "running" ? <Loader2 size={13} className="animate-spin" /> : tool.status === "error" ? <XCircle size={13} className="text-[#D02020]" /> : <CheckCircle2 size={13} className="text-[#207A3A]" />}
            <Wrench size={13} />
            <span className="break-all">{tool.toolName}</span>
            <Chip size="sm" className="ml-auto border border-black bg-[var(--surface-muted)] text-[10px] text-black">
              {tool.status === "running" ? "运行中" : tool.status === "error" ? "出错" : "完成"}
            </Chip>
          </div>
          {tool.summary && <p className="mt-1 break-words font-medium leading-5 text-black/60">{tool.summary}</p>}
        </div>
      ))}
    </div>
  );
}

export function ProposalList({
  proposals,
  resolvedIds,
  loading,
  onConfirm,
  onReject,
}: {
  proposals: Record<string, Record<string, unknown>>;
  resolvedIds: Set<string>;
  loading: boolean;
  onConfirm: (proposalId: string) => void;
  onReject: (proposalId: string) => void;
}) {
  const rows = Object.values(proposals).filter((proposal) => {
    const id = proposalId(proposal);
    return id && !resolvedIds.has(id);
  });
  if (rows.length === 0) return null;
  return (
    <div className="space-y-2">
      {rows.map((proposal) => {
        const id = proposalId(proposal);
        return (
          <div key={id} className="border-2 border-black bg-white px-3 py-2 text-xs text-black">
            <div className="flex items-start gap-2">
              <AlertTriangle size={15} className="mt-0.5 shrink-0 text-[#D02020]" />
              <div className="min-w-0 flex-1">
                <p className="break-words font-black">{proposalSummary(proposal)}</p>
                <p className="mt-1 font-semibold text-black/55">{riskLabel(proposal)}</p>
                {planGroupText(proposal) && <p className="mt-1 break-all font-semibold text-[#2060D0]">{planGroupText(proposal)}</p>}
                {recoveryText(proposal) && <p className="mt-1 font-medium text-black/60">{recoveryText(proposal)}</p>}
                {affectedRecordsText(proposal) && (
                  <p className="mt-1 break-words font-medium text-black/60">{affectedRecordsText(proposal)}</p>
                )}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                size="sm"
                isDisabled={loading}
                onPress={() => onConfirm(id)}
                className="bauhaus-button bauhaus-button-red !min-h-8 !px-3 !py-1 !text-xs"
              >
                确认
              </Button>
              <Button
                size="sm"
                isDisabled={loading}
                onPress={() => onReject(id)}
                className="bauhaus-button bauhaus-button-outline !min-h-8 !px-3 !py-1 !text-xs"
              >
                拒绝
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

const MANUAL_REVIEW_RESOLUTIONS: { value: ManualReviewResolution; label: string; description: string }[] = [
  { value: "effect_absent_retry", label: "确认无副作用并重试", description: "仅当证据表明外部效果未发生时使用。" },
  { value: "effect_present_accept", label: "确认效果已发生并接受", description: "将已观察到的外部效果视为本节点成功。" },
  { value: "compensation_completed", label: "确认补偿已完成", description: "确认人工或外部补偿已经完成。" },
  { value: "abort_plan", label: "终止计划", description: "停止计划，不再自动推进剩余节点。" },
];

function availableManualReviewResolutions(reviewCase: ManualReviewCase) {
  const restricted =
    String(reviewCase.subject_type || "") === "saga_compensation" || !String(reviewCase.node_id || "");
  if (!restricted) return MANUAL_REVIEW_RESOLUTIONS;
  return MANUAL_REVIEW_RESOLUTIONS.filter(
    (option) => option.value === "compensation_completed" || option.value === "abort_plan"
  );
}

function manualReviewIdempotencyKey(
  reviewCase: ManualReviewCase,
  resolution: ManualReviewResolution,
  reviewerNote: string,
  evidenceRef = ""
): string {
  // The durable resolution/audit columns cap idempotency keys at 160 chars, so the
  // decision material is folded into two 32-bit digests instead of being concatenated.
  const material = [
    String(reviewCase.case_id || ""),
    String(reviewCase.case_generation ?? ""),
    String(reviewCase.evidence_digest || ""),
    resolution,
    reviewerNote,
    evidenceRef.trim(),
  ].join("\u0000");
  let digestA = 2166136261;
  let digestB = 40389;
  for (let index = 0; index < material.length; index += 1) {
    const code = material.charCodeAt(index);
    digestA = Math.imul(digestA ^ code, 16777619);
    digestB = Math.imul(digestB ^ code, 2246822519);
  }
  return [
    "manual-review",
    reviewCase.case_id,
    reviewCase.case_generation,
    (digestA >>> 0).toString(16),
    (digestB >>> 0).toString(16),
  ].join(":");
}

async function resolveManualReviewWithFreshFence(
  caseId: string,
  sessionId: string,
  resolution: ManualReviewResolution,
  reviewerNote: string,
  compensationEvidenceRef = "",
  effectPresentTypedOutputsJson = "",
  effectPresentManifestDigest = "",
  effectPresentProviderReference = ""
): Promise<{ refreshed: ManualReviewCase; result: ManualReviewResolutionResponse }> {
  const refreshed = await harnessAgentApi.getManualReviewCase(caseId, sessionId);
  if (!Number.isInteger(refreshed.case_generation) || !refreshed.evidence_digest) {
    throw new Error("人工复核详情缺少 case_generation 或 evidence_digest，无法安全提交。");
  }
  let typedOutputs: Record<string, unknown> | undefined;
  if (resolution === "effect_present_accept") {
    try {
      const parsed = JSON.parse(effectPresentTypedOutputsJson.trim());
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
      typedOutputs = parsed as Record<string, unknown>;
    } catch {
      throw new Error("effect_present_accept 的 typed_outputs 必须是合法 JSON 对象。");
    }
    if (!effectPresentManifestDigest.trim() && !effectPresentProviderReference.trim()) {
      throw new Error("effect_present_accept 必须提供 effect manifest digest 或 provider reference。");
    }
  }
  const evidenceFingerprint = JSON.stringify({
    compensationEvidenceRef: compensationEvidenceRef.trim(),
    typed_outputs: typedOutputs || {},
    effect_manifest_digest: effectPresentManifestDigest.trim(),
    provider_reference: effectPresentProviderReference.trim(),
  });
  const result = await harnessAgentApi.resolveManualReviewCase(caseId, {
    session_id: sessionId,
    resolution,
    case_generation: refreshed.case_generation as number,
    evidence_digest: refreshed.evidence_digest,
    idempotency_key: manualReviewIdempotencyKey(refreshed, resolution, reviewerNote.trim(), evidenceFingerprint),
    evidence: {
      reviewer_note: reviewerNote.trim(),
      reviewed_evidence_digest: refreshed.evidence_digest,
      ...(resolution === "compensation_completed" && compensationEvidenceRef.trim()
        ? { provider_reference: compensationEvidenceRef.trim() }
        : {}),
      ...(resolution === "effect_present_accept"
        ? {
            typed_outputs: typedOutputs || {},
            ...(effectPresentManifestDigest.trim() ? { effect_manifest_digest: effectPresentManifestDigest.trim() } : {}),
            ...(effectPresentProviderReference.trim() ? { provider_reference: effectPresentProviderReference.trim() } : {}),
          }
        : {}),
    },
  });
  return { refreshed, result };
}

export function ManualReviewCaseList({
  sessionId,
  cases,
  onCasesLoaded,
  onResolution,
}: {
  sessionId: string | null;
  cases: Record<string, ManualReviewCase>;
  onCasesLoaded: (cases: ManualReviewCase[]) => void;
  onResolution: (response: ManualReviewResolutionResponse) => void;
}) {
  const [selected, setSelected] = useState<Record<string, ManualReviewResolution | undefined>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [compensationEvidenceRefs, setCompensationEvidenceRefs] = useState<Record<string, string>>({});
  const [effectPresentTypedOutputs, setEffectPresentTypedOutputs] = useState<Record<string, string>>({});
  const [effectPresentManifestDigest, setEffectPresentManifestDigest] = useState<Record<string, string>>({});
  const [effectPresentProviderReference, setEffectPresentProviderReference] = useState<Record<string, string>>({});
  const [busyCaseId, setBusyCaseId] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const onCasesLoadedRef = useRef(onCasesLoaded);
  onCasesLoadedRef.current = onCasesLoaded;

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    setRefreshing(true);
    setError("");
    void harnessAgentApi.listManualReviewCases(sessionId)
      .then(async (summaries) => {
        if (cancelled) return;
        onCasesLoadedRef.current(summaries);
        const details = await Promise.all(
          summaries.map(async (summary) => {
            try {
              return await harnessAgentApi.getManualReviewCase(summary.case_id, sessionId);
            } catch {
              return summary;
            }
          })
        );
        if (!cancelled) onCasesLoadedRef.current(details);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setRefreshing(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const casesMissingFence = Object.values(cases)
    .filter((reviewCase) => !Number.isInteger(reviewCase.case_generation) || !reviewCase.evidence_digest)
    .map((reviewCase) => reviewCase.case_id)
    .sort()
    .join("|");

  useEffect(() => {
    if (!sessionId || !casesMissingFence) return;
    let cancelled = false;
    const caseIds = casesMissingFence.split("|");
    void Promise.all(caseIds.map((caseId) => harnessAgentApi.getManualReviewCase(caseId, sessionId)))
      .then((details) => {
        if (!cancelled) onCasesLoadedRef.current(details);
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [casesMissingFence, sessionId]);
  const rows = Object.values(cases).sort((left, right) => {
    const leftResolved = String(left.status || "open") === "resolved" ? 1 : 0;
    const rightResolved = String(right.status || "open") === "resolved" ? 1 : 0;
    return leftResolved - rightResolved || String(left.case_id).localeCompare(String(right.case_id));
  });
  if (rows.length === 0 && !refreshing && !error) return null;

  const submitResolution = async (reviewCase: ManualReviewCase) => {
    const resolution = selected[reviewCase.case_id];
    if (!sessionId || !resolution || busyCaseId) return;
    setBusyCaseId(reviewCase.case_id);
    setError("");
    try {
      const { refreshed, result } = await resolveManualReviewWithFreshFence(
        reviewCase.case_id,
        sessionId,
        resolution,
        notes[reviewCase.case_id] || "",
        compensationEvidenceRefs[reviewCase.case_id] || "",
        effectPresentTypedOutputs[reviewCase.case_id] || "",
        effectPresentManifestDigest[reviewCase.case_id] || "",
        effectPresentProviderReference[reviewCase.case_id] || ""
      );
      onCasesLoaded([refreshed]);
      onResolution(result);
      setSelected((current) => ({ ...current, [reviewCase.case_id]: undefined }));
      setCompensationEvidenceRefs((current) => ({ ...current, [reviewCase.case_id]: "" }));
      setEffectPresentTypedOutputs((current) => ({ ...current, [reviewCase.case_id]: "" }));
      setEffectPresentManifestDigest((current) => ({ ...current, [reviewCase.case_id]: "" }));
      setEffectPresentProviderReference((current) => ({ ...current, [reviewCase.case_id]: "" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyCaseId("");
    }
  };

  return (
    <section className="space-y-3 border-t-2 border-black bg-[#FFF4D8] px-4 py-3" aria-label="人工复核事项">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-black text-black">Manual review · 人工复核</p>
          <p className="mt-1 text-xs font-semibold text-black/65">请根据原因、效果与证据明确选择处理方式；不会由 Agent 自动决定。</p>
        </div>
        {refreshing && <Loader2 size={15} className="mt-0.5 shrink-0 animate-spin" aria-label="正在刷新人工复核详情" />}
      </div>
      {rows.map((reviewCase) => {
        const caseId = reviewCase.case_id;
        const status = String(reviewCase.status || "open");
        const resolved = status === "resolved" || status === "closed";
        const hasFence = Number.isInteger(reviewCase.case_generation) && Boolean(reviewCase.evidence_digest);
        const selectedResolution = selected[caseId];
        return (
          <article key={caseId} className="border-2 border-black bg-white p-3 text-xs text-black">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="break-all font-black">复核事项 {caseId}</p>
                <p className="mt-1 break-all font-semibold text-black/55">
                  计划 {reviewCase.plan_id || "-"} · 节点 {reviewCase.node_id || "-"}
                </p>
              </div>
              <Chip size="sm" className="rounded-none border border-black bg-[#FFF4D8] text-[10px] font-bold text-black">
                {resolved ? `已处理 · ${reviewCase.resolution || status}` : status}
              </Chip>
            </div>
            <dl className="mt-3 grid gap-2">
              <div>
                <dt className="font-black">原因 reason_code</dt>
                <dd className="mt-0.5 whitespace-pre-wrap break-words text-black/70">{safePreview(reviewCase.reason ?? reviewCase.reason_code ?? "-")}</dd>
              </div>
              <div>
                <dt className="font-black">效果 effect_state</dt>
                <dd className="mt-0.5 whitespace-pre-wrap break-words text-black/70">{safePreview(reviewCase.effect ?? reviewCase.effect_state ?? "-")}</dd>
              </div>
              <div>
                <dt className="font-black">证据 evidence</dt>
                <dd><pre className="mt-0.5 max-h-36 overflow-auto whitespace-pre-wrap break-words border border-black/15 bg-[#f6f4ee] p-2 font-mono text-[11px] leading-5 text-black/70">{safePreview(reviewCase.evidence ?? reviewCase.evidence_json ?? {})}</pre></dd>
              </div>
            </dl>
            <p className="mt-2 break-all font-mono text-[10px] text-black/50">
              case_generation={reviewCase.case_generation ?? "待加载"} · evidence_digest={reviewCase.evidence_digest || "待加载"}
            </p>
            {!resolved && (
              <div className="mt-3 space-y-2 border-t border-black/15 pt-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  {availableManualReviewResolutions(reviewCase).map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      disabled={Boolean(busyCaseId)}
                      onClick={() => setSelected((current) => ({ ...current, [caseId]: option.value }))}
                      className={`border-2 border-black p-2 text-left transition-colors ${selectedResolution === option.value ? "bg-[#2060D0] text-white" : "bg-white text-black hover:bg-[#EEF3FF]"}`}
                    >
                      <span className="block font-black">{option.label}</span>
                      <span className={`mt-1 block text-[10px] leading-4 ${selectedResolution === option.value ? "text-white/80" : "text-black/55"}`}>{option.description}</span>
                      <span className="sr-only">{option.value}</span>
                    </button>
                  ))}
                </div>
                <Textarea
                  value={notes[caseId] || ""}
                  onValueChange={(value) => setNotes((current) => ({ ...current, [caseId]: value }))}
                  minRows={1}
                  maxRows={3}
                  label="人工证据说明（可选）"
                  variant="bordered"
                  isDisabled={Boolean(busyCaseId)}
                />
                {selectedResolution === "effect_present_accept" && (
                  <div className="space-y-2 border border-black/20 bg-white p-2">
                    <Textarea
                      value={effectPresentTypedOutputs[caseId] || ""}
                      onValueChange={(value) => setEffectPresentTypedOutputs((current) => ({ ...current, [caseId]: value }))}
                      minRows={2}
                      maxRows={6}
                      label="typed_outputs JSON（必填）"
                      placeholder={'例如：{"primary_record_id": 123}'}
                      variant="bordered"
                      isDisabled={Boolean(busyCaseId)}
                    />
                    <Textarea
                      value={effectPresentManifestDigest[caseId] || ""}
                      onValueChange={(value) => setEffectPresentManifestDigest((current) => ({ ...current, [caseId]: value }))}
                      minRows={1}
                      maxRows={2}
                      label="effect manifest digest（与外部引用至少填一项）"
                      variant="bordered"
                      isDisabled={Boolean(busyCaseId)}
                    />
                    <Textarea
                      value={effectPresentProviderReference[caseId] || ""}
                      onValueChange={(value) => setEffectPresentProviderReference((current) => ({ ...current, [caseId]: value }))}
                      minRows={1}
                      maxRows={2}
                      label="provider reference（与 digest 至少填一项）"
                      variant="bordered"
                      isDisabled={Boolean(busyCaseId)}
                    />
                  </div>
                )}
                {selectedResolution === "compensation_completed" && (
                  <Textarea
                    value={compensationEvidenceRefs[caseId] || ""}
                    onValueChange={(value) => setCompensationEvidenceRefs((current) => ({ ...current, [caseId]: value }))}
                    minRows={1}
                    maxRows={2}
                    label="补偿凭证/摘要/外部引用（必填）"
                    placeholder="输入 compensation receipt、result digest、effect manifest digest 或 provider reference"
                    variant="bordered"
                    isDisabled={Boolean(busyCaseId)}
                  />
                )}
                <Button
                  size="sm"
                  isDisabled={
                    !sessionId ||
                    !hasFence ||
                    !selectedResolution ||
                    Boolean(busyCaseId) ||
                    (selectedResolution === "compensation_completed" && !compensationEvidenceRefs[caseId]?.trim()) ||
                    (selectedResolution === "effect_present_accept" && (
                      !effectPresentTypedOutputs[caseId]?.trim() ||
                      (!effectPresentManifestDigest[caseId]?.trim() && !effectPresentProviderReference[caseId]?.trim())
                    ))
                  }
                  onPress={() => void submitResolution(reviewCase)}
                  className="bauhaus-button bauhaus-button-red !min-h-9 !px-3 !py-1 !text-xs"
                >
                  {busyCaseId === caseId ? <Loader2 size={13} className="animate-spin" /> : null}
                  提交人工决策
                </Button>
                {!hasFence && <p className="font-semibold text-[#D02020]">正在加载最新 generation/digest fence，加载完成前不可提交。</p>}
              </div>
            )}
          </article>
        );
      })}
      {error && <p className="border border-[#D02020] bg-white p-2 text-xs font-semibold text-[#D02020]">{error}</p>}
    </section>
  );
}

export function PlanExecutionList({ plans }: { plans: Record<string, PlanExecutionState> }) {
  const items = Object.values(plans).sort((a, b) => a.planId.localeCompare(b.planId));
  if (!items.length) return null;
  const failureStatuses = new Set(["failed", "blocked", "rejected", "manual_review", "partially_completed"]);
  return (
    <section className="space-y-2" aria-label="Plan execution results">
      {items.map((plan) => {
        const groups = Object.values(plan.groups);
        const nodes = Object.values(plan.nodes);
        const failed = nodes.filter((node) => failureStatuses.has(String(node.status || "")));
        return (
          <article key={plan.planId} className="border-2 border-black bg-[#F6F4EE] p-3 text-xs text-black shadow-[2px_2px_0_0_rgba(18,18,18,0.18)]">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-black">Plan {plan.planId}</p>
              <Chip size="sm" className="rounded-none border border-black bg-white font-bold">{plan.effectiveStatus || plan.status}</Chip>
            </div>
            {plan.completionReason && <p className="mt-1 font-semibold text-black/70">completion_reason: {plan.completionReason}</p>}
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {groups.map((group) => (
                <div key={group.group_id} className="border border-black/30 bg-white p-2">
                  <p className="font-bold">Group {group.group_id} · {group.status}</p>
                  {group.result_receipt_id && <p className="break-all">result_receipt_id: {group.result_receipt_id}</p>}
                  {group.result_digest && <p className="break-all">result_digest: {group.result_digest}</p>}
                </div>
              ))}
            </div>
            <div className="mt-2 space-y-1">
              {nodes.map((node) => (
                <div key={node.node_id} className={`border px-2 py-1 ${failureStatuses.has(String(node.status || "")) ? "border-[#D02020] bg-white" : "border-black/20 bg-white"}`}>
                  <p className="font-bold">Node {node.node_id} · {node.status}</p>
                  <p>effect_state: {node.effect_state || "unknown"} · completion_reason: {node.completion_reason || ""}</p>
                  {node.manual_review_case_id && <p className="break-all text-[#D02020]">manual_review_case_id: {node.manual_review_case_id}</p>}
                  {node.effect_manifest_digest && <p className="break-all text-black/60">effect_manifest_digest: {node.effect_manifest_digest}</p>}
                </div>
              ))}
            </div>
            {failed.length > 0 && <p className="mt-2 font-black text-[#D02020]">部分或全部节点未成功完成，请查看失败节点或人工复核。</p>}
          </article>
        );
      })}
    </section>
  );
}


export function AgentCardList({ cards }: { cards: unknown[] }) {
  if (!cards.length) return null;
  return (
    <div className="space-y-2">
      {cards.map((card, index) => (
        <Card key={index} className="rounded-none border-2 border-black bg-white shadow-none">
          <CardBody className="p-3 text-xs text-black">
            <div className="flex items-start gap-2">
              <FileText size={15} className="mt-0.5 shrink-0 text-[#2060D0]" />
              <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words font-sans text-xs leading-5 text-black/70">
                {safePreview(card)}
              </pre>
            </div>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

function safePreview(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    const text = JSON.stringify(value, null, 2);
    return text.length > 600 ? `${text.slice(0, 600)}...` : text;
  } catch {
    return String(value);
  }
}

function normalizeDisplayRole(role: string): DisplayAgentMessage["role"] {
  if (
    role === "user" ||
    role === "assistant" ||
    role === "toolResult" ||
    role === "compactionSummary" ||
    role === "branchSummary" ||
    role === "custom"
  ) {
    return role;
  }
  return "custom";
}
