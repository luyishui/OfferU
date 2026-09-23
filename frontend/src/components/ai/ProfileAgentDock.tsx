"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Chip, Input, ScrollShadow, Textarea } from "@nextui-org/react";
import {
  Bot,
  ChevronDown,
  History,
  Loader2,
  Plus,
  Send,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { useSWRConfig } from "swr";
import { bauhausFieldClassNames } from "@/lib/bauhaus";
import {
  agentStreamReducer,
  applyProposalDecisionResponse,
  createInitialAgentStreamState,
  proposalDecisionUiTransition,
  type AgentStreamState,
  type DisplayAgentMessage,
} from "@/lib/agentStreamReducer";
import {
  harnessAgentApi,
  profileAgentChatStream,
  profileApi,
  type AgentStreamEvent,
  type ProfileAgentSessionSummary,
} from "@/lib/api";
import {
  AgentStreamMessageBubble,
  ProposalList,
  StreamingAssistantBubble,
  ToolExecutionList,
  makeDisplayMessage,
} from "./AgentStreamView";
import { useDraggableDock } from "./useDraggableDock";


function welcomeState(): AgentStreamState {
  return {
    ...createInitialAgentStreamState(),
    messages: [
      makeDisplayMessage(
        "assistant",
        "把简历和目标岗位给我，我会先建一版档案，再围绕缺口继续追问。所有写入都会先变成可确认的提案。",
        "welcome"
      ),
    ],
  };
}

export function ProfileAgentDock() {
  const { mutate } = useSWRConfig();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [open, setOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historySessions, setHistorySessions] = useState<ProfileAgentSessionSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationTitle, setConversationTitle] = useState("新建档案对话");
  const [agentState, setAgentState] = useState<AgentStreamState>(welcomeState);
  const [targetRole, setTargetRole] = useState("");
  const [targetCity, setTargetCity] = useState("");
  const [jobGoal, setJobGoal] = useState("");
  const [resumeText, setResumeText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [resolvedProposalIds, setResolvedProposalIds] = useState<Set<string>>(new Set());
  const [confirmationChallenges, setConfirmationChallenges] = useState<Record<string, string>>({});
  const { dockRef, dockStyle, dragHandleProps, launcherDragHandleProps, consumeDragClick } =
    useDraggableDock<HTMLDivElement>({ width: 460, height: 720 });

  const canStart = Boolean(file || resumeText.trim() || targetRole.trim() || jobGoal.trim());

  const refreshHistory = async () => {
    try {
      const result = await profileApi.listProfileAgentSessions(50);
      setHistorySessions(result.sessions || []);
    } catch {
      setHistorySessions([]);
    }
  };

  useEffect(() => {
    if (open) void refreshHistory();
  }, [open]);

  const refreshProfile = () => {
    void mutate((key) => typeof key === "string" && key.includes("/api/profile/"));
  };

  const dispatchStreamEvent = (event: AgentStreamEvent) => {
    setAgentState((prev) => agentStreamReducer(prev, event));
    if (event.type === "final" && event.conversation_id) {
      setConversationId(event.conversation_id);
      setConversationTitle(`档案对话 ${event.conversation_id}`);
    }
  };

  const startAgent = async () => {
    if (!canStart || loading) return;
    setLoading(true);
    setError("");
    try {
      const result = await profileApi.startProfileAgent({
        file,
        resume_text: resumeText,
        target_role: targetRole,
        target_city: targetCity,
        job_goal: jobGoal,
      });
      const sessionId = String(result.session_id || result.conversation_id || "");
      if (sessionId) {
        setConversationId(sessionId);
        setConversationTitle(`档案对话 ${sessionId}`);
      }
      const proposals = Array.isArray(result.proposals) ? result.proposals : [];
      setAgentState((prev) => {
        let next = agentStreamReducer(
          { ...prev, status: "done" },
          { type: "final", ...result, conversation_id: sessionId || result.conversation_id } as AgentStreamEvent
        );
        for (const proposal of proposals) {
          next = agentStreamReducer(next, { type: "proposal", proposal });
        }
        if (result.assistant_message) {
          next = {
            ...next,
            messages: [
              ...next.messages,
              makeDisplayMessage("assistant", String(result.assistant_message)),
            ],
          };
        }
        return next;
      });
      await refreshHistory();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "AI 建档启动失败");
    } finally {
      setLoading(false);
    }
  };

  const sendMessage = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || loading) return;
    if (!conversationId) {
      setError("请先上传简历或填写目标岗位，启动建档会话。");
      return;
    }
    setInput("");
    setLoading(true);
    setError("");

    const userMessage = makeDisplayMessage("user", content);
    setAgentState((prev) => ({
      ...prev,
      messages: [...prev.messages, userMessage],
      status: "streaming",
      error: "",
    }));

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await profileAgentChatStream(
        { session_id: conversationId, message: content },
        { onEvent: dispatchStreamEvent },
        controller.signal
      );
      refreshProfile();
      await refreshHistory();
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message || "AI 回复失败");
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
    }
  };

  const confirmProposal = async (proposalId: string) => {
    if (loading || !conversationId) return;
    setLoading(true);
    setError("");
    try {
      const challenge = confirmationChallenges[proposalId];
      const result = await harnessAgentApi.confirmProposal(
        proposalId,
        conversationId,
        challenge ? { confirmation_challenge: challenge } : {}
      );
      const transition = proposalDecisionUiTransition(
        resolvedProposalIds,
        confirmationChallenges,
        proposalId,
        result
      );
      setResolvedProposalIds(transition.resolvedProposalIds);
      setConfirmationChallenges(transition.confirmationChallenges);
      if (result.continuation || result.next_proposals || result.plan_event) {
        setAgentState((prev) => applyProposalDecisionResponse(prev, result));
      }
      refreshProfile();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "确认提案失败");
    } finally {
      setLoading(false);
    }
  };

  const rejectProposal = async (proposalId: string) => {
    if (loading || !conversationId) return;
    setLoading(true);
    setError("");
    try {
      const result = await harnessAgentApi.rejectProposal(proposalId, conversationId);
      setAgentState((prev) => applyProposalDecisionResponse(prev, result));
      const transition = proposalDecisionUiTransition(
        resolvedProposalIds,
        confirmationChallenges,
        proposalId,
        result
      );
      setResolvedProposalIds(transition.resolvedProposalIds);
      setConfirmationChallenges(transition.confirmationChallenges);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "拒绝提案失败");
    } finally {
      setLoading(false);
    }
  };

  const resetSession = () => {
    abortRef.current?.abort();
    setConversationId(null);
    setConversationTitle("新建档案对话");
    setResolvedProposalIds(new Set());
    setConfirmationChallenges({});
    setFile(null);
    setResumeText("");
    setInput("");
    setError("");
    setHistoryOpen(false);
    setAgentState(welcomeState());
  };

  const loadHistorySession = async (id: string) => {
    setError("");
    try {
      const session = await profileApi.getProfileAgentSession(id);
      const messages = (session.messages_json || [])
        .filter((item) => item?.role === "user" || item?.role === "assistant")
        .map((item, index): DisplayAgentMessage => ({
          id: `history-${id}-${index}`,
          role: item.role as DisplayAgentMessage["role"],
          text: String(item.content || ""),
        }));
      setConversationId(id);
      setConversationTitle(session.title || `档案对话 ${id}`);
      setResolvedProposalIds(new Set());
      setConfirmationChallenges({});
      setAgentState({
        ...createInitialAgentStreamState(),
        messages: messages.length
          ? messages
          : [makeDisplayMessage("assistant", "已打开历史对话。")],
      });
      setHistoryOpen(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载历史对话失败");
    }
  };

  useEffect(() => {
    const handleOpenProfileAgent = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId?: string | number }>).detail || {};
      setOpen(true);
      if (detail.sessionId) {
        void loadHistorySession(String(detail.sessionId));
      } else {
        void refreshHistory();
      }
    };
    window.addEventListener("offeru:open-profile-agent", handleOpenProfileAgent);
    return () => window.removeEventListener("offeru:open-profile-agent", handleOpenProfileAgent);
  }, []);

  return (
    <div
      ref={dockRef}
      style={dockStyle}
      className="fixed bottom-5 right-5 z-[80] flex flex-col items-end gap-3"
    >
      {open && (
        <section className="bauhaus-panel flex h-[min(82dvh,720px)] w-[min(460px,calc(100vw-2rem))] flex-col overflow-hidden bg-white">
          <header {...dragHandleProps} className="cursor-move select-none border-b border-black/10 p-4 touch-none">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <div className="bauhaus-panel-sm flex h-12 w-12 shrink-0 items-center justify-center bg-[#F0C020] text-black">
                  <Bot size={22} />
                </div>
                <div className="min-w-0">
                  <button
                    type="button"
                    onClick={() => setHistoryOpen((value) => !value)}
                    className="flex max-w-[230px] items-center gap-1 text-left text-[11px] font-black uppercase tracking-[0.08em] text-black/65 hover:text-black"
                    title="打开历史对话"
                  >
                    <History size={12} />
                    <span className="truncate">{conversationTitle}</span>
                  </button>
                  <h2 className="mt-1 truncate text-2xl font-black text-black">AI 求职助手</h2>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  isIconOnly
                  size="sm"
                  variant="light"
                  aria-label="收起 AI 助手"
                  className="text-black"
                  onPress={() => setOpen(false)}
                >
                  <ChevronDown size={17} />
                </Button>
                <Button
                  isIconOnly
                  size="sm"
                  variant="light"
                  aria-label="重置 AI 助手"
                  className="text-black"
                  onPress={resetSession}
                >
                  <Trash2 size={16} />
                </Button>
              </div>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <Chip variant="flat" className="bauhaus-chip border-2 border-black bg-[var(--surface-muted)] px-3 py-2 text-black">
                档案建模
              </Chip>
              <Chip variant="flat" className="bauhaus-chip border-2 border-black bg-[#F7E4E1] px-3 py-2 text-black">
                {loading ? "处理中" : "就绪"}
              </Chip>
            </div>
          </header>

          {historyOpen && (
            <div className="border-b-2 border-black bg-white px-4 py-3">
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-black text-black">历史对话</p>
                <Button
                  size="sm"
                  startContent={<Plus size={13} />}
                  onPress={resetSession}
                  className="h-8 border-2 border-black bg-[#F0C020] px-2 text-xs font-black text-black"
                >
                  新建
                </Button>
              </div>
              <div className="max-h-40 space-y-2 overflow-y-auto">
                {historySessions.length === 0 && (
                  <p className="border border-black/20 bg-[var(--surface-muted)] px-3 py-2 text-xs font-semibold text-black/60">
                    暂无历史对话
                  </p>
                )}
                {historySessions.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    onClick={() => void loadHistorySession(session.id)}
                    className={`w-full border px-3 py-2 text-left ${
                      session.id === conversationId ? "border-black bg-[#FFF4D8]" : "border-black/20 bg-white"
                    }`}
                  >
                    <p className="truncate text-xs font-black text-black">{session.title || "档案对话"}</p>
                    <p className="mt-0.5 text-[11px] font-medium text-black/55">
                      {session.message_count} 条 / {session.last_message}
                    </p>
                  </button>
                ))}
              </div>
            </div>
          )}

          <ScrollShadow className="flex-1 overflow-y-auto p-4">
            {!conversationId && (
              <div className="bauhaus-panel-sm mb-4 space-y-3 bg-white p-3">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.docx,.txt"
                  className="hidden"
                  onChange={(event) => setFile(event.target.files?.[0] || null)}
                />
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <Input size="sm" label="目标岗位" value={targetRole} onValueChange={setTargetRole} variant="bordered" classNames={bauhausFieldClassNames} />
                  <Input size="sm" label="目标城市" value={targetCity} onValueChange={setTargetCity} variant="bordered" classNames={bauhausFieldClassNames} />
                </div>
                <Input size="sm" label="求职偏好" value={jobGoal} onValueChange={setJobGoal} variant="bordered" classNames={bauhausFieldClassNames} />
                <Textarea
                  minRows={2}
                  maxRows={4}
                  label="粘贴简历文本"
                  value={resumeText}
                  onValueChange={setResumeText}
                  variant="bordered"
                  classNames={bauhausFieldClassNames}
                />
                <div className="flex items-center gap-2">
                  <Button
                    variant="light"
                    startContent={<Upload size={15} />}
                    onPress={() => fileInputRef.current?.click()}
                    className="bauhaus-button bauhaus-button-outline !min-h-10 !min-w-0 !justify-start !px-3 !py-2 !text-xs"
                  >
                    <span className="max-w-40 truncate">{file ? file.name : "上传简历"}</span>
                  </Button>
                  <Button
                    isDisabled={!canStart}
                    isLoading={loading}
                    onPress={startAgent}
                    className="bauhaus-button bauhaus-button-yellow !min-h-10 !px-3 !py-2 !text-xs"
                  >
                    开始建档
                  </Button>
                </div>
              </div>
            )}

            <div className="space-y-4">
              {agentState.messages.map((message) => (
                <AgentStreamMessageBubble key={message.id} message={message} compact />
              ))}
              {agentState.streaming && <StreamingAssistantBubble streaming={agentState.streaming} compact />}
              {loading && !agentState.streaming && (
                <div className="inline-flex items-center gap-2 border-2 border-black bg-white px-4 py-3 text-[15px] font-medium text-black/65 shadow-[2px_2px_0_0_rgba(18,18,18,0.3)]">
                  <Loader2 size={13} className="animate-spin" />
                  <span>AI 正在整理...</span>
                </div>
              )}
              <ToolExecutionList executions={agentState.toolExecutions} />
            </div>

            <div className="mt-4">
              <ProposalList
                proposals={agentState.proposals}
                resolvedIds={resolvedProposalIds}
                loading={loading}
                onConfirm={(id) => void confirmProposal(id)}
                onReject={(id) => void rejectProposal(id)}
              />
            </div>
          </ScrollShadow>

          {error && <div className="border-t border-black/10 bg-[#D02020] px-4 py-2 text-xs font-medium text-white">{error}</div>}

          <footer className="bauhaus-panel-sm border-x-0 border-b-0 bg-white p-3">
            <div className="flex items-end gap-2">
              <Textarea
                value={input}
                onValueChange={setInput}
                minRows={1}
                maxRows={3}
                placeholder="补充经历、成果数据或求职偏好"
                variant="bordered"
                className="flex-1"
                classNames={bauhausFieldClassNames}
                isDisabled={!conversationId || loading}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
              />
              <Button
                isIconOnly
                aria-label="发送给 AI 建档助手"
                isDisabled={!conversationId || !input.trim() || loading}
                onPress={() => void sendMessage()}
                className="bauhaus-button bauhaus-button-red !mb-[2px] !min-h-11 !min-w-11 !px-0 !py-0"
              >
                {loading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
              </Button>
            </div>
          </footer>
        </section>
      )}

      <Button
        isIconOnly
        aria-label="打开 AI 求职助手"
        {...launcherDragHandleProps}
        className="h-14 w-14 cursor-move touch-none border-2 border-black bg-[#F0C020] text-black shadow-[3px_3px_0_0_rgba(18,18,18,0.25)]"
        onPress={() => {
          if (consumeDragClick()) return;
          setOpen((prev) => !prev);
        }}
      >
        {open ? <X size={22} /> : <Bot size={24} />}
      </Button>
    </div>
  );
}
