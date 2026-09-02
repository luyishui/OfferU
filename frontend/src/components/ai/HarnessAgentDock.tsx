"use client";

import { useEffect, useRef, useState } from "react";
import { Button, Chip, ScrollShadow, Textarea } from "@nextui-org/react";
import {
  Bot,
  Download,
  GitBranch,
  History,
  Loader2,
  Plus,
  Send,
  Sparkles,
  Trash2,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import {
  agentChatStream,
  harnessAgentApi,
  type AgentConversationTree,
  type AgentStreamEvent,
  type HarnessAgentConversationSummary,
} from "@/lib/api";
import {
  agentStreamReducer,
  applyProposalDecisionResponse,
  applyManualReviewResolutionResponse,
  mergeManualReviewCases,
  proposalDecisionUiTransition,
  createInitialAgentStreamState,
  type AgentStreamState,
  type DisplayAgentMessage,
} from "@/lib/agentStreamReducer";
import { bauhausFieldClassNames } from "@/lib/bauhaus";
import {
  AgentCardList,
  ManualReviewCaseList,
  PlanExecutionList,
  AgentStreamMessageBubble,
  ProposalList,
  StreamingAssistantBubble,
  ToolExecutionList,
  apiMessagesFromDisplay,
  makeDisplayMessage,
  messagesFromTree,
} from "./AgentStreamView";
import { useDraggableDock } from "./useDraggableDock";

const QUICK_ACTIONS = [
  { label: "确认身份", prompt: "先问我几个问题，判断我是校招/应届/实习，还是社招/跳槽" },
  { label: "校招体检", prompt: "按校招标准检查我的档案、简历、岗位和投递流程缺口" },
  { label: "每日岗位", prompt: "今天给我推荐一个最值得投的校招/实习岗位，并说明为什么" },
  { label: "异常检测", prompt: "检查我的档案、岗位库、投递管理和面试日程有没有异常" },
];

const STAGE_LABELS: Record<string, string> = {
  campus: "校招",
  experienced: "社招",
  unknown: "待确认",
};

function welcomeState(): AgentStreamState {
  return {
    ...createInitialAgentStreamState(),
    messages: [
      makeDisplayMessage(
        "assistant",
        "我是 OfferU 全局助手。现在我会先识别你是校招还是社招，再主动检查档案、岗位、简历、投递和面试日程里的风险。",
        "welcome"
      ),
    ],
  };
}

export function HarnessAgentDock() {
  const [open, setOpen] = useState(false);
  const [agentState, setAgentState] = useState<AgentStreamState>(welcomeState);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [importedStage, setImportedStage] = useState<string>("unknown");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversationTitle, setConversationTitle] = useState("新对话");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);
  const [tree, setTree] = useState<AgentConversationTree | null>(null);
  const [conversations, setConversations] = useState<HarnessAgentConversationSummary[]>([]);
  const [resolvedProposalIds, setResolvedProposalIds] = useState<Set<string>>(new Set());
  const [confirmationChallenges, setConfirmationChallenges] = useState<Record<string, string>>({});
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const { dockRef, dockStyle, dragHandleProps, launcherDragHandleProps, consumeDragClick } = useDraggableDock({
    width: 440,
    height: 600,
  });

  const latestStage = String(agentState.finalResponse?.user_stage || importedStage || "unknown");
  const latestMode = loading ? "streaming" : agentState.finalResponse?.stop_reason || agentState.status || "ready";

  const refreshConversations = async () => {
    try {
      const result = await harnessAgentApi.conversations();
      setConversations(result.conversations || []);
    } catch {
      setConversations([]);
    }
  };

  useEffect(() => {
    if (open) void refreshConversations();
  }, [open]);

  const dispatchStreamEvent = (event: AgentStreamEvent) => {
    setAgentState((prev) => agentStreamReducer(prev, event));
    if (event.type === "final" && event.conversation_id) {
      setConversationId(event.conversation_id);
      setConversationTitle(event.conversation_id);
    }
  };

  const sendMessage = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || loading) return;

    const userMessage = makeDisplayMessage("user", content);
    const nextMessages = [...agentState.messages, userMessage];
    setAgentState((prev) => ({ ...prev, messages: nextMessages, status: "streaming", error: "" }));
    setInput("");
    setLoading(true);
    setError("");

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await agentChatStream(
        {
          messages: apiMessagesFromDisplay(nextMessages),
          conversation_id: conversationId,
        },
        {
          onEvent: dispatchStreamEvent,
          onError: (streamError) => setError(streamError.message || "全局助手流式响应失败"),
        },
        controller.signal
      );
      await refreshConversations();
    } catch (err: any) {
      if (err?.name !== "AbortError") setError(err.message || "全局助手请求失败");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
    }
  };

  const startNewConversation = () => {
    abortRef.current?.abort();
    setConversationId(null);
    setConversationTitle("新对话");
    setResolvedProposalIds(new Set());
    setConfirmationChallenges({});
    setHistoryOpen(false);
    setTreeOpen(false);
    setTree(null);
    setAgentState({
      ...createInitialAgentStreamState(),
      messages: [
        makeDisplayMessage(
          "assistant",
          "新对话已开始。先告诉我你是校招/应届/实习，还是社招/跳槽，我会按对应路径主动检查。",
          `welcome-${Date.now()}`
        ),
      ],
    });
  };

  const loadConversation = async (id: string) => {
    setError("");
    try {
      const conversation = await harnessAgentApi.conversation(id);
      const bootstrap = await harnessAgentApi.sessionBootstrap(conversation.id);
      setConversationId(conversation.id);
      setConversationTitle(conversation.title || "历史对话");
      setResolvedProposalIds(new Set());
      setConfirmationChallenges(Object.fromEntries(
        (bootstrap.proposals || [])
          .filter((proposal) => String(proposal.status || "") === "awaiting_next_confirmation")
          .map((proposal) => [String(proposal.proposal_id || ""), String(proposal.confirmation_challenge || "")])
          .filter(([proposalId, challenge]) => Boolean(proposalId && challenge))
      ));
      setHistoryOpen(false);
      let restoredState: AgentStreamState = {
        ...createInitialAgentStreamState(),
        messages: (conversation.messages || []).map((message, index): DisplayAgentMessage => ({
          id: `${conversation.id}-${index}`,
          role: message.role,
          text: message.content,
        })),
      };
      for (const proposal of bootstrap.proposals || []) {
        restoredState = agentStreamReducer(restoredState, {
          type: "proposal",
          proposal_id: String(proposal.proposal_id || ""),
          proposal,
        });
      }
      for (const planEvent of bootstrap.plan_events || []) {
        restoredState = agentStreamReducer(restoredState, planEvent);
      }
      setAgentState(restoredState);
    } catch (err: any) {
      setError(err.message || "加载历史对话失败");
    }
  };

  const removeConversation = async (id: string) => {
    setError("");
    try {
      await harnessAgentApi.deleteConversation(id);
      if (conversationId === id) startNewConversation();
      await refreshConversations();
    } catch (err: any) {
      setError(err.message || "删除历史对话失败");
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
    } catch (err: any) {
      setError(err.message || "确认 proposal 失败");
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
    } catch (err: any) {
      setError(err.message || "拒绝 proposal 失败");
    } finally {
      setLoading(false);
    }
  };

  const exportMemory = async () => {
    setError("");
    try {
      const result = await harnessAgentApi.exportMemory("markdown");
      await navigator.clipboard.writeText(String(result.content || ""));
      setAgentState((prev) => ({
        ...prev,
        messages: [...prev.messages, makeDisplayMessage("assistant", "已把当前 Agent 记忆导出为 Markdown，并放到剪贴板。")],
      }));
    } catch (err: any) {
      setError(err.message || "导出记忆失败");
    }
  };

  const importMemoryFile = async (file: File) => {
    setError("");
    try {
      const text = await file.text();
      const result = await harnessAgentApi.importMemory(text);
      setImportedStage(result.memory.user_stage);
      setAgentState((prev) => ({
        ...prev,
        messages: [
          ...prev.messages,
          makeDisplayMessage(
            "assistant",
            `已导入本地记忆。当前识别为：${STAGE_LABELS[result.memory.user_stage] || result.memory.user_stage}。`
          ),
        ],
      }));
    } catch (err: any) {
      setError(err.message || "导入记忆失败");
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const refreshTree = async (): Promise<AgentConversationTree | null> => {
    if (!conversationId) return null;
    try {
      const nextTree = await harnessAgentApi.getConversationTree(conversationId);
      setTree(nextTree);
      return nextTree;
    } catch (err: any) {
      setError(err.message || "加载会话树失败");
      return null;
    }
  };

  const toggleTree = async () => {
    const next = !treeOpen;
    setTreeOpen(next);
    if (next) await refreshTree();
  };

  const navigateTree = async (entryId: string) => {
    if (!conversationId || loading) return;
    setError("");
    try {
      await harnessAgentApi.navigateConversationTree(conversationId, entryId);
      const refreshed = await refreshTree();
      if (!refreshed) return;
      setAgentState((prev) => ({
        ...prev,
        messages: messagesFromTree(refreshed),
      }));
    } catch (err: any) {
      setError(err.message || "导航会话树失败");
    }
  };

  if (!open) {
    return (
      <Button
        isIconOnly
        aria-label="打开 OfferU 全局助手"
        title="打开 OfferU 全局助手。按住可拖动。"
        {...launcherDragHandleProps}
        onPress={() => {
          if (consumeDragClick()) return;
          setOpen(true);
        }}
        style={dockStyle}
        className="fixed bottom-24 right-5 z-50 h-14 w-14 cursor-move touch-none border-2 border-black bg-[#F0C020] text-black shadow-[4px_4px_0_0_rgba(18,18,18,0.35)] md:bottom-6"
      >
        <Bot size={22} />
      </Button>
    );
  }

  return (
    <section
      ref={dockRef}
      style={dockStyle}
      className="fixed bottom-24 right-4 z-50 flex max-h-[82vh] w-[min(92vw,440px)] flex-col overflow-hidden border-2 border-black bg-white shadow-[6px_6px_0_0_rgba(18,18,18,0.35)] md:bottom-6 md:right-6"
    >
      <header
        {...dragHandleProps}
        className="flex cursor-move select-none items-center justify-between border-b-2 border-black bg-[var(--surface-muted)] px-4 py-3 touch-none"
      >
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center border-2 border-black bg-[#F0C020] text-black">
            <Sparkles size={17} />
          </div>
          <div className="min-w-0">
            <button
              type="button"
              onClick={() => setHistoryOpen((value) => !value)}
              className="flex max-w-[180px] items-center gap-1 text-left text-[11px] font-black uppercase tracking-[0.08em] text-black/65 hover:text-black"
              title="打开历史对话"
            >
              <History size={12} />
              <span className="truncate">{conversationTitle || "历史对话"}</span>
            </button>
            <h2 className="truncate text-base font-black text-black">OfferU 全局助手</h2>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Chip size="sm" className="border border-black bg-white text-[10px] font-semibold text-black">
            <UserRound size={12} />
            {STAGE_LABELS[latestStage] || latestStage}
          </Chip>
          <Chip size="sm" className="border border-black bg-white text-[10px] font-semibold text-black">
            {latestMode}
          </Chip>
          <Button isIconOnly size="sm" variant="light" aria-label="关闭助手" onPress={() => setOpen(false)} className="min-w-8 text-black">
            <X size={16} />
          </Button>
        </div>
      </header>

      {historyOpen && (
        <div className="border-b-2 border-black bg-white px-4 py-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-black text-black">历史对话</p>
            <div className="flex gap-2">
              <Button
                isIconOnly
                size="sm"
                aria-label="会话树"
                isDisabled={!conversationId}
                onPress={() => void toggleTree()}
                className="h-8 min-w-8 border-2 border-black bg-white text-black"
              >
                <GitBranch size={13} />
              </Button>
              <Button size="sm" startContent={<Plus size={13} />} onPress={startNewConversation} className="h-8 border-2 border-black bg-[#F0C020] px-2 text-xs font-black text-black">
                新建
              </Button>
            </div>
          </div>
          {treeOpen && tree && (
            <div className="mb-3 max-h-40 space-y-2 overflow-y-auto border border-black/20 bg-[var(--surface-muted)] p-2">
              {tree.entries.map((entry) => (
                <button
                  key={entry.entry_id}
                  type="button"
                  onClick={() => void navigateTree(entry.entry_id)}
                  className={`w-full border px-2 py-1 text-left text-[11px] ${
                    entry.entry_id === tree.leaf_id ? "border-black bg-[#FFF4D8]" : "border-black/20 bg-white"
                  }`}
                >
                  <span className="font-black">{entry.entry_type}</span>
                  <span className="ml-2 break-words text-black/60">{entry.preview}</span>
                </button>
              ))}
            </div>
          )}
          <div className="max-h-40 space-y-2 overflow-y-auto">
            {conversations.length === 0 && (
              <p className="border border-black/20 bg-[var(--surface-muted)] px-3 py-2 text-xs font-semibold text-black/60">
                暂无历史对话
              </p>
            )}
            {conversations.map((conversation) => (
              <div
                key={conversation.id}
                className={`flex items-center gap-2 border px-2 py-2 ${
                  conversation.id === conversationId ? "border-black bg-[#FFF4D8]" : "border-black/20 bg-white"
                }`}
              >
                <button type="button" onClick={() => void loadConversation(conversation.id)} className="min-w-0 flex-1 text-left">
                  <p className="truncate text-xs font-black text-black">{conversation.title || "历史对话"}</p>
                  <p className="mt-0.5 truncate text-[11px] font-medium text-black/55">
                    {conversation.message_count} 条 / {conversation.last_message}
                  </p>
                </button>
                <Button isIconOnly size="sm" variant="light" aria-label="删除历史对话" onPress={() => void removeConversation(conversation.id)} className="min-w-8 text-[#D02020]">
                  <Trash2 size={14} />
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-2 border-b border-black/10 px-4 py-3">
        {QUICK_ACTIONS.map((action) => (
          <Chip key={action.label} className="cursor-pointer border-2 border-black bg-white px-2 text-xs font-semibold text-black" onClick={() => void sendMessage(action.prompt)}>
            {action.label}
          </Chip>
        ))}
      </div>

      <div className="flex items-center justify-between border-b border-black/10 px-4 py-2">
        <p className="text-[11px] font-semibold text-black/65">可导入 Codex / Claude Code / 本地 Markdown 或 JSON 记忆</p>
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".md,.markdown,.json,.txt"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void importMemoryFile(file);
            }}
          />
          <Button isIconOnly size="sm" variant="light" aria-label="导入本地记忆" onPress={() => fileInputRef.current?.click()} className="min-w-8 text-black">
            <Upload size={15} />
          </Button>
          <Button isIconOnly size="sm" variant="light" aria-label="导出助手记忆" onPress={() => void exportMemory()} className="min-w-8 text-black">
            <Download size={15} />
          </Button>
        </div>
      </div>

      <ScrollShadow className="min-h-[19rem] flex-1 overflow-y-auto px-4 py-3">
        <div className="space-y-4">
          {agentState.messages.map((message) => (
            <AgentStreamMessageBubble key={message.id} message={message} compact />
          ))}
          {agentState.streaming && <StreamingAssistantBubble streaming={agentState.streaming} compact />}
          {loading && !agentState.streaming && (
            <div className="inline-flex items-center gap-2 border-2 border-black bg-white px-3 py-2 text-sm font-medium text-black/65">
              <Loader2 size={14} className="animate-spin" />
              正在连接 Agent...
            </div>
          )}
          <ToolExecutionList executions={agentState.toolExecutions} />
          <AgentCardList cards={agentState.cards} />
        </div>
      </ScrollShadow>

      <div className="border-t border-black/10 bg-[#F7E4E1] px-4 py-3">
        <PlanExecutionList plans={agentState.plans} />

      <ProposalList
          proposals={agentState.proposals}
          resolvedIds={resolvedProposalIds}
          loading={loading}
          onConfirm={(id) => void confirmProposal(id)}
          onReject={(id) => void rejectProposal(id)}
        />
      </div>

      <ManualReviewCaseList
        sessionId={conversationId}
        cases={agentState.manualReviewCases}
        onCasesLoaded={(cases) => setAgentState((prev) => mergeManualReviewCases(prev, cases))}
        onResolution={(result) => setAgentState((prev) => applyManualReviewResolutionResponse(prev, result))}
      />
      {error && <div className="border-t border-black bg-[#D02020] px-4 py-2 text-xs font-semibold text-white">{error}</div>}

      <footer className="border-t-2 border-black bg-white p-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onValueChange={setInput}
            minRows={1}
            maxRows={3}
            placeholder="告诉我你是校招还是社招，或者直接说你要推进哪一步..."
            variant="bordered"
            className="flex-1"
            classNames={bauhausFieldClassNames}
            isDisabled={loading}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void sendMessage();
              }
            }}
          />
          <Button isIconOnly aria-label="发送" onPress={() => void sendMessage()} isDisabled={!input.trim() || loading} className="bauhaus-button bauhaus-button-red !min-h-10 !min-w-10 !px-0 !py-0">
            {loading ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
          </Button>
        </div>
      </footer>
    </section>
  );
}
