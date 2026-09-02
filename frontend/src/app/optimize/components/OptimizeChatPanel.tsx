"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import Link from "next/link";
import MarkdownIt from "markdown-it";
import { Button } from "@nextui-org/react";
import { FileText, MessageSquare, Play, SendHorizonal, Square } from "lucide-react";
import { streamOptimizeAgentChat, OptimizeAgentStreamEvent } from "@/lib/hooks";
import { harnessAgentApi, type AgentStreamEvent } from "@/lib/api";
import { markConfirmRequestProcessed } from "@/lib/agentStreamReducer";
import { cleanRichHtml } from "@/app/resume/components/templates/shared";
import { ManualReviewCaseList, PlanExecutionList } from "@/components/ai/AgentStreamView";
import {
  agentStreamReducer,
  applyManualReviewResolutionResponse,
  applyProposalDecisionResponse,
  createInitialAgentStreamState,
  mergeManualReviewCases,
  type AgentStreamState,
} from "@/lib/agentStreamReducer";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  (typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "http://127.0.0.1:8000");

const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  typographer: true,
});

function preprocessMarkdown(content: string): string {
  return content
    .replace(/\*\*\s+(.+?)\s+\*\*/g, "**$1**")
    .replace(/\*\s+(.+?)\s+\*/g, "*$1*");
}

interface Suggestion {
  type: string;
  section_title?: string;
  original: string;
  suggested: string;
  reason: string;
  injected_keywords?: string[];
  matched_jd_requirements?: string[];
  interview_reference?: string;
  diff?: {
    deleted: string[];
    added: string[];
  };
}

interface ConfirmRequestData {
  tool: string;
  args: Record<string, any>;
  summary: string;
  risk?: unknown;
  affectedRecords?: unknown[];
  processed?: boolean;
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  suggestions?: Suggestion[];
  resume_id?: number;
  confirmRequest?: ConfirmRequestData;
}

interface OptimizeChatPanelProps {
  jobIds: number[];
  mode: "per_job" | "combined";
  disabled: boolean;
  profileId: number | null;
  loadSessionId?: string | null;
  onLoadSessionConsumed?: () => void;
}

let _msgCounter = 0;
function nextMsgId(): string {
  return `msg_${++_msgCounter}_${Date.now().toString(36)}`;
}

function RenderedMarkdown({ content }: { content: string }) {
  const html = useMemo(() => md.render(preprocessMarkdown(content)), [content]);
  return (
    <div
      className="prose-chat"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

/** Render sanitized HTML content (for diff added / suggested content) */
function SafeHtmlContent({ content, className }: { content: string; className?: string }) {
  const html = useMemo(() => cleanRichHtml(content), [content]);
  return (
    <div
      className={className}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function formatRisk(value: unknown): string {
  if (value === undefined || value === null || value === "") return "需要确认";
  return `风险: ${String(value)}`;
}

function formatAffectedRecords(records: unknown[] | undefined): string {
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

export function OptimizeChatPanel({ jobIds, mode, disabled, profileId, loadSessionId, onLoadSessionConsumed }: OptimizeChatPanelProps) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [phase, setPhase] = useState<string>("idle");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [progressLabel, setProgressLabel] = useState<string>("");
  // Backend-issued confirmation challenges, keyed by proposal_id. A second
  // confirmation for a risk>=5 Plan group must echo the stored challenge.
  const [confirmationChallenges, setConfirmationChallenges] = useState<Record<string, string>>({});
  // Durable Plan/Group/proposal execution state (same reducer contract as the
  // global Agent page / Harness dock).
  const [durableState, setDurableState] = useState<AgentStreamState>(() => createInitialAgentStreamState());
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamingMsgIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, progressLabel]);

  // Load an existing session when loadSessionId is provided
  useEffect(() => {
    if (!loadSessionId) return;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const res = await fetch(`${API_BASE}/api/optimize/agent/sessions/${loadSessionId}`, {
          credentials: "include",
        });
        if (!res.ok) {
          const errBody = await res.json().catch(() => null);
          throw new Error(errBody?.detail || `加载会话失败 (${res.status})`);
        }
        const data = await res.json();
        if (!cancelled) {
          setSessionId(data.session_id);
          setPhase(data.phase || "idle");
          const history: ChatMessage[] = (data.messages || []).map((m: any, i: number) => {
            const cr = m.confirm_request as ConfirmRequestData | undefined;
            return {
              id: nextMsgId(),
              role: m.role === "user" ? "user" as const : "assistant" as const,
              content: m.content || "",
              suggestions: m.suggestions,
              resume_id: m.resume_id,
              // Mark all historical confirm requests as processed
              confirmRequest: cr ? { ...cr, processed: true } : undefined,
            };
          });

          // Durable restore: pending Plan/Group proposal cards + plan_status
          // envelopes (legacy session text is history only; confirmation
          // authority comes from durable proposal state).
          const durable = data.durable;
          const challenges: Record<string, string> = {};
          if (durable) {
            if (durable.recovery_error) {
              history.push({
                id: nextMsgId(),
                role: "assistant",
                content: `会话恢复提示: ${durable.recovery_error}`,
              });
            }
            for (const proposal of durable.proposals || []) {
              const proposalId = String(proposal.proposal_id || "");
              if (
                proposalId &&
                String(proposal.status || "") === "awaiting_next_confirmation"
              ) {
                const challenge = String(proposal.confirmation_challenge || "");
                if (challenge) challenges[proposalId] = challenge;
              }
              history.push({
                id: nextMsgId(),
                role: "assistant",
                content: String(proposal.summary || "需要确认 proposal 后继续。"),
                confirmRequest: {
                  tool: "proposal",
                  args: { proposal_id: proposalId, proposal },
                  summary: String(proposal.summary || "需要确认 proposal 后继续。"),
                  risk: proposal.risk || proposal.risk_level,
                  affectedRecords: proposal.affected_records || proposal.affectedRecords,
                },
              });
            }
          }

          setMessages(history);
          if (Object.keys(challenges).length > 0) {
            setConfirmationChallenges((prev) => ({ ...prev, ...challenges }));
          }
          // Replay durable envelopes through the shared reducer so Plan/Group
          // execution results and manual-review cases are restored identically
          // to the Agent page / Harness dock after a reload.
          let restored = createInitialAgentStreamState();
          for (const proposal of durable?.proposals || []) {
            restored = agentStreamReducer(restored, {
              type: "proposal",
              proposal_id: String(proposal.proposal_id || ""),
              proposal,
            } as AgentStreamEvent);
          }
          for (const planEvent of durable?.plan_events || []) {
            restored = agentStreamReducer(restored, planEvent);
          }
          setDurableState(restored);
        }
      } catch (err: any) {
        if (!cancelled) {
          setMessages([{ id: nextMsgId(), role: "assistant", content: `加载会话失败: ${err.message}` }]);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          onLoadSessionConsumed?.();
        }
      }
    };
    void load();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- onLoadSessionConsumed is a stable callback; omitting to avoid re-triggering
  }, [loadSessionId]);

  const startSession = async () => {
    if (jobIds.length === 0) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/optimize/agent/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ job_ids: jobIds, mode, profile_id: profileId }),
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => null);
        throw new Error(errBody?.detail || `请求失败 (${res.status})`);
      }
      const data = await res.json();
      if (data.session_id) {
        setSessionId(data.session_id);
        setPhase(data.phase || "confirming");
        if (data.assistant_message) {
          setMessages([{ id: nextMsgId(), role: "assistant", content: data.assistant_message }]);
        }
      }
    } catch (err: any) {
      setMessages([{ id: nextMsgId(), role: "assistant", content: `启动失败: ${err.message}` }]);
    } finally {
      setLoading(false);
    }
  };

  // Shared stream event handler used by both sendMessage and sendConfirmAction
  const handleStreamEvent = useCallback((event: OptimizeAgentStreamEvent) => {
    // token event — append to streaming message
    if (event.token) {
      const tokenText = event.token;
      setProgressLabel("");
      if (!streamingMsgIdRef.current) {
        const msgId = nextMsgId();
        streamingMsgIdRef.current = msgId;
        setMessages((prev) => [...prev, { id: msgId, role: "assistant", content: tokenText }]);
      } else {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === streamingMsgIdRef.current
              ? { ...msg, content: msg.content + tokenText }
              : msg
          )
        );
      }
      return;
    }

    // progress event
    if (event.progress && event.label) {
      setProgressLabel(event.label);
    }

    // error event
    if (event.error) {
      streamingMsgIdRef.current = null;
      setMessages((prev) => [
        ...prev,
        {
          id: nextMsgId(),
          role: "assistant",
          content: event.message || `分析出错: ${event.error}`,
        },
      ]);
    }

    // assistant message (final response — replace streaming message if exists)
    if (event.assistant_message) {
      const assistantContent = event.assistant_message;
      const streamingId = streamingMsgIdRef.current;
      streamingMsgIdRef.current = null;
      if (streamingId) {
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === streamingId
              ? {
                  ...msg,
                  content: assistantContent,
                  suggestions: event.suggestions,
                  resume_id: event.resume_id,
                }
              : msg
          )
        );
      } else {
        setMessages((prev) => [
          ...prev,
          {
            id: nextMsgId(),
            role: "assistant",
            content: assistantContent,
            suggestions: event.suggestions,
            resume_id: event.resume_id,
          },
        ]);
      }
    }

    // phase update
    if (event.phase) {
      setPhase(event.phase);
    }

    // durable plan status — merge Plan/Group/node execution state through the
    // shared reducer; success is shown from the durable envelope, not text alone
    if (event.plan_status) {
      setDurableState((prev) =>
        agentStreamReducer(prev, { ...event.plan_status, type: "plan_status" } as AgentStreamEvent)
      );
    }

    // confirm_request event
    if (event.confirm_request) {
      const cr = event.confirm_request;
      streamingMsgIdRef.current = null;
      const proposalId = String(cr.args?.proposal_id || "");
      const challenge = String(cr.args?.confirmation_challenge || "");
      if (proposalId && challenge) {
        setConfirmationChallenges((prev) => ({ ...prev, [proposalId]: challenge }));
      }
      const confirmData: ConfirmRequestData = {
        tool: cr.tool,
        args: cr.args,
        summary: cr.summary,
        risk: cr.risk,
        affectedRecords: cr.affected_records,
      };
      setMessages((prev) => [
        ...prev,
        {
          id: nextMsgId(),
          role: "assistant",
          content: cr.summary,
          confirmRequest: confirmData,
        },
      ]);
    }
  }, []);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || !sessionId || loading) return;

    const userMsg: ChatMessage = { id: nextMsgId(), role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setProgressLabel("AI 正在思考...");

    // abort any previous request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamOptimizeAgentChat(
        { session_id: sessionId, message: text, action: "reply" },
        {
          signal: controller.signal,
          onEvent: handleStreamEvent,
        }
      );
    } catch (err: any) {
      if (err.name === "AbortError") return;
      setMessages((prev) => [
        ...prev,
        { id: nextMsgId(), role: "assistant", content: `发送失败: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
      setProgressLabel("");
      streamingMsgIdRef.current = null;
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }, [input, sessionId, loading, handleStreamEvent]);

  const sendConfirmAction = useCallback(async (action: "confirm" | "reject", requestedProposalId = "") => {
    if (!sessionId || loading) return;

    const label = action === "confirm" ? "确认" : "取消";
    const activeRequestMessage = messages.find((msg) => {
      if (!msg.confirmRequest || msg.confirmRequest.processed) return false;
      const proposalId = String(msg.confirmRequest.args?.proposal_id || "");
      return requestedProposalId ? proposalId === requestedProposalId : true;
    });
    if (!activeRequestMessage?.confirmRequest) return;
    const activeProposalId = String(activeRequestMessage.confirmRequest.args?.proposal_id || "");
    const userMsg: ChatMessage = { id: nextMsgId(), role: "user", content: label };
    setMessages((prev) => [...prev, userMsg]);

    const markActiveProcessed = () => {
      setMessages((prev) => activeProposalId
        ? markConfirmRequestProcessed(prev, activeProposalId)
        : prev.map((msg) => msg.id === activeRequestMessage.id
          ? { ...msg, confirmRequest: msg.confirmRequest ? { ...msg.confirmRequest, processed: true } : undefined }
          : msg));
    };

    setLoading(true);
    setProgressLabel("AI 正在处理...");

    if (typeof activeProposalId === "string" && activeProposalId) {
      try {
        // Risk>=5 Plan groups require a second confirmation that echoes the
        // backend-issued challenge (same contract as agent page / dock).
        const challenge = confirmationChallenges[activeProposalId];
        const result = action === "confirm"
          ? await harnessAgentApi.confirmProposal(
              activeProposalId,
              sessionId,
              challenge ? { confirmation_challenge: challenge } : {}
            )
          : await harnessAgentApi.rejectProposal(activeProposalId, sessionId);
        if (String(result.status || "") !== "awaiting_next_confirmation") {
          markActiveProcessed();
        }
        // Keep the card actionable when the backend requests a second
        // confirmation; retain the fresh challenge for the next click.
        if (String(result.status || "") === "awaiting_next_confirmation" && result.confirmation_challenge) {
          setConfirmationChallenges((prev) => ({
            ...prev,
            [activeProposalId]: String(result.confirmation_challenge),
          }));
        } else {
          setConfirmationChallenges((prev) => {
            const next = { ...prev };
            delete next[activeProposalId];
            return next;
          });
        }
        if (result.plan_event) {
          setDurableState((prev) => applyProposalDecisionResponse(prev, result));
        }
        const continuation = result.continuation;
        if (continuation?.assistant_message) {
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "assistant",
              content: continuation.assistant_message,
            },
          ]);
        }
        if (continuation?.ok === false) {
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "assistant",
              content: "当前会话正在处理，已确认的结果会在稍后自动跟进。",
            },
          ]);
        }
        const nextProposal = continuation?.proposals?.[0] as Record<string, any> | undefined;
        const nextProposalId = nextProposal?.proposal_id || nextProposal?.id;
        if (nextProposalId) {
          setMessages((prev) => [
            ...prev,
            {
              id: nextMsgId(),
              role: "assistant",
              content: String(nextProposal.summary || "需要确认 proposal 后继续。"),
              confirmRequest: {
                tool: "proposal",
                args: { proposal_id: nextProposalId, proposal: nextProposal },
                summary: String(nextProposal.summary || "需要确认 proposal 后继续。"),
                risk: nextProposal.risk || nextProposal.risk_level,
                affectedRecords: nextProposal.affected_records || nextProposal.affectedRecords,
              },
            },
          ]);
        }
      } catch (err: any) {
        setMessages((prev) => [
          ...prev,
          { id: nextMsgId(), role: "assistant", content: `操作失败: ${err.message}` },
        ]);
      } finally {
        setLoading(false);
        setProgressLabel("");
      }
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamOptimizeAgentChat(
        { session_id: sessionId, message: label, action },
        {
          signal: controller.signal,
          onEvent: handleStreamEvent,
        }
      );
      markActiveProcessed();
    } catch (err: any) {
      if (err.name === "AbortError") return;
      setMessages((prev) => [
        ...prev,
        { id: nextMsgId(), role: "assistant", content: `操作失败: ${err.message}` },
      ]);
    } finally {
      setLoading(false);
      setProgressLabel("");
      streamingMsgIdRef.current = null;
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }, [sessionId, loading, messages, confirmationChallenges, handleStreamEvent]);

  // cleanup on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleStopGeneration = useCallback(() => {
    abortRef.current?.abort();
    // Append interrupted indicator to the current streaming message
    const streamingId = streamingMsgIdRef.current;
    if (streamingId) {
      setMessages((prev) =>
        prev.map((msg) =>
          msg.id === streamingId
            ? { ...msg, content: msg.content + "\n\n*[已中断]*" }
            : msg
        )
      );
    }
    setLoading(false);
    streamingMsgIdRef.current = null;
    setProgressLabel("");
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void sendMessage();
    }
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b border-black/12 p-5 md:p-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="bauhaus-label text-black/60">步骤三 · AI 对话优化</p>
            <h2 className="mt-2 text-3xl font-bold leading-tight md:text-4xl">智能优化工作流</h2>
          </div>
          <div className="bauhaus-panel-sm bg-[#e4ece6] px-4 py-3 text-black">
            <p className="bauhaus-label text-black/55">阶段</p>
            <p className="mt-2 text-sm font-bold">
              {phase === "idle"
                ? "待启动"
                : phase === "confirming"
                  ? "确认中"
                  : phase === "analyzing"
                    ? "分析中"
                    : phase === "framework"
                      ? "框架确认"
                      : phase === "rewriting"
                        ? "逐段改写"
                        : phase === "completed"
                          ? "已完成"
                          : phase}
            </p>
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto p-5 md:p-6 custom-scrollbar"
        >
          {messages.length === 0 && (
            <div className="flex min-h-64 flex-col items-center justify-center gap-4 text-center">
              <MessageSquare size={48} className="text-black/20" />
              <p className="text-sm font-medium text-black/50">
                选择岗位后点击「开始优化」，AI 将引导你逐步完成简历定制。
              </p>
              <Button
                className="bauhaus-button bauhaus-button-red"
                startContent={<Play size={16} />}
                onPress={startSession}
                isDisabled={disabled || jobIds.length === 0 || loading}
                isLoading={loading}
              >
                开始优化
              </Button>
            </div>
          )}

          <div className="space-y-4">
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-[85%] border border-black/15 p-4 text-sm leading-relaxed shadow-[1px_1px_0_0_rgba(18,18,18,0.08)] ${
                    msg.role === "user"
                      ? "bg-[#f3ead2] text-black"
                      : "bg-white text-black"
                  }`}
                >
                  {msg.role === "assistant" ? (
                    <>
                      <RenderedMarkdown content={msg.content} />

                      {msg.suggestions && msg.suggestions.length > 0 && (
                        <div className="mt-3 space-y-3">
                          {msg.suggestions.map((sug, idx) => (
                            <div
                              key={idx}
                              className="border border-black/10 bg-[var(--surface-muted)] p-3"
                            >
                              {sug.section_title && (
                                <p className="bauhaus-label text-black/55 mb-2">{sug.section_title}</p>
                              )}

                              {sug.diff && sug.diff.deleted.length > 0 ? (
                                <div className="space-y-2">
                                  <div>
                                    <p className="text-xs font-semibold text-black/50 mb-1">删除内容</p>
                                    {sug.diff.deleted.map((d, i) => (
                                      <del
                                        key={i}
                                        className="prose-chat text-sm leading-relaxed block"
                                        style={{ color: "#999" }}
                                      >
                                        {d}
                                      </del>
                                    ))}
                                  </div>
                                  <div>
                                    <p className="text-xs font-semibold text-black/50 mb-1">新增内容</p>
                                    {sug.diff.added.map((a, i) => (
                                      <SafeHtmlContent
                                        key={i}
                                        content={a}
                                        className="text-sm leading-relaxed prose-chat"
                                      />
                                    ))}
                                  </div>
                                </div>
                              ) : (
                                <div className="space-y-1">
                                  <p className="text-xs font-semibold text-black/50">原文</p>
                                  <p className="text-sm text-black/60 line-through">{sug.original}</p>
                                  <p className="text-xs font-semibold text-black/50 mt-1">建议</p>
                                  <SafeHtmlContent
                                    content={sug.suggested}
                                    className="text-sm leading-relaxed prose-chat"
                                  />
                                </div>
                              )}

                              {sug.reason && (
                                <p className="mt-2 text-xs text-black/55">💡 {sug.reason}</p>
                              )}
                              {sug.matched_jd_requirements && sug.matched_jd_requirements.length > 0 && (
                                <p className="mt-1 text-xs text-black/50">
                                  匹配JD要求: {sug.matched_jd_requirements.join("、")}
                                </p>
                              )}
                              {sug.injected_keywords && sug.injected_keywords.length > 0 && (
                                <p className="mt-1 text-xs text-black/50">
                                  注入关键词: {sug.injected_keywords.join("、")}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {msg.confirmRequest && (
                        <div className="mt-3 border-2 border-black/15 bg-[#f3ead2] p-4">
                          <p className="text-sm font-bold text-black">{msg.confirmRequest.summary}</p>
                          <div className="mt-2 space-y-1 text-xs font-semibold text-black/60">
                            <p>{formatRisk(msg.confirmRequest.risk)}</p>
                            {formatAffectedRecords(msg.confirmRequest.affectedRecords) && (
                              <p className="break-words">
                                影响记录: {formatAffectedRecords(msg.confirmRequest.affectedRecords)}
                              </p>
                            )}
                          </div>
                          <div className="mt-3 flex gap-2">
                            <Button
                              size="sm"
                              className="bauhaus-button bauhaus-button-red !min-h-8"
                              onPress={() => void sendConfirmAction("confirm", String(msg.confirmRequest?.args?.proposal_id || ""))}
                              isDisabled={loading || msg.confirmRequest.processed}
                            >
                              确认
                            </Button>
                            <Button
                              size="sm"
                              className="bauhaus-button bauhaus-button-outline !min-h-8"
                              onPress={() => void sendConfirmAction("reject", String(msg.confirmRequest?.args?.proposal_id || ""))}
                              isDisabled={loading || msg.confirmRequest.processed}
                            >
                              取消
                            </Button>
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="whitespace-pre-wrap">{msg.content}</div>
                  )}

                  {msg.resume_id && (
                    <Link
                      href={`/resume/${msg.resume_id}`}
                      className="mt-3 flex items-center gap-3 border border-black/15 bg-[var(--surface-muted)] p-3 transition-transform hover:-translate-y-0.5"
                    >
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-black bg-[#e4ece6]">
                        <FileText size={18} className="text-black" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-bold text-black">打开定制简历</p>
                        <p className="mt-0.5 text-xs font-medium text-black/55">
                          点击进入编辑器
                        </p>
                      </div>
                    </Link>
                  )}
                </div>
              </div>
            ))}

            {loading && progressLabel && messages.length > 0 && (
              <div className="flex justify-start">
                <div className="border border-black/15 bg-[#e4ece6] px-4 py-3 text-sm text-black/70 shadow-[1px_1px_0_0_rgba(18,18,18,0.08)]">
                  <span className="inline-block animate-pulse">●</span>{" "}
                  {progressLabel}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Durable Plan/Group/proposal execution results and manual-review
            surface — same reducer/view contract as the Agent page / dock. */}
        {sessionId && (
          <>
            <PlanExecutionList plans={durableState.plans} />
            <ManualReviewCaseList
              sessionId={sessionId}
              cases={durableState.manualReviewCases}
              onCasesLoaded={(cases) => setDurableState((prev) => mergeManualReviewCases(prev, cases))}
              onResolution={(result) => setDurableState((prev) => applyManualReviewResolutionResponse(prev, result))}
            />
          </>
        )}

        {sessionId && (
          <div className="shrink-0 border-t border-black/12 p-4">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void sendMessage();
              }}
              className="flex items-end gap-2"
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
                rows={1}
                className="flex-1 resize-none border border-black/15 bg-white px-3 py-2 text-sm text-black placeholder:text-black/40 focus:outline-none focus:ring-1 focus:ring-black/20"
                disabled={loading}
              />
              {loading ? (
                <button
                  type="button"
                  onClick={handleStopGeneration}
                  className="flex h-9 w-9 shrink-0 items-center justify-center border border-red-400 bg-red-500 text-white transition-colors hover:bg-red-600"
                >
                  <Square size={14} fill="currentColor" />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  className="flex h-9 w-9 shrink-0 items-center justify-center border border-black/15 bg-[var(--surface-muted)] text-black/60 transition-colors hover:bg-[#e4ece6] hover:text-black disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <SendHorizonal size={16} />
                </button>
              )}
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
