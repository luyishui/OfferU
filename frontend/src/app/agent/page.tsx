"use client";

import { useMemo, useRef, useState } from "react";
import { Button, Chip, ScrollShadow, Textarea } from "@nextui-org/react";
import { Bot, GitBranch, Loader2, Send, Sparkles, Trash2 } from "lucide-react";
import {
  agentChatStream,
  harnessAgentApi,
  type AgentConversationTree,
  type AgentStreamEvent,
} from "@/lib/api";
import {
  agentStreamReducer,
  applyProposalDecisionResponse,
  applyManualReviewResolutionResponse,
  mergeManualReviewCases,
  proposalDecisionUiTransition,
  createInitialAgentStreamState,
  type AgentStreamState,
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
} from "@/components/ai/AgentStreamView";

const QUICK_ACTIONS = [
  { label: "职业探索", prompt: "参考我的档案，给我 5 个意想不到但适合我的职业方向" },
  { label: "岗位匹配", prompt: "帮我看看现在岗位库里适合投哪些岗位" },
  { label: "简历准备", prompt: "帮我为最适合的岗位准备定制简历" },
  { label: "投递跟进", prompt: "帮我梳理投递管理和下一步动作" },
  { label: "面试日程", prompt: "帮我检查邮件通知和面试日程" },
];

const welcome = makeDisplayMessage(
  "assistant",
  "我是 OfferU 全局助手。你可以直接给我一个目标，我会读取档案、岗位、简历、投递和日程上下文，然后把下一步拆成可确认的动作。",
  "welcome"
);

function initialState(): AgentStreamState {
  return { ...createInitialAgentStreamState(), messages: [welcome] };
}

export default function AgentPage() {
  const [agentState, setAgentState] = useState<AgentStreamState>(initialState);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [resolvedProposalIds, setResolvedProposalIds] = useState<Set<string>>(new Set());
  const [confirmationChallenges, setConfirmationChallenges] = useState<Record<string, string>>({});
  const [treeOpen, setTreeOpen] = useState(false);
  const [tree, setTree] = useState<AgentConversationTree | null>(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const latestMode = useMemo(() => {
    if (loading) return "流式响应中";
    if (agentState.status === "error") return "出错";
    return agentState.finalResponse?.stop_reason || "就绪";
  }, [agentState.finalResponse?.stop_reason, agentState.status, loading]);

  const dispatchStreamEvent = (event: AgentStreamEvent) => {
    setAgentState((prev) => agentStreamReducer(prev, event));
    if (event.type === "final" && event.conversation_id) setConversationId(event.conversation_id);
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
          onError: (streamError) => {
            setError(streamError.message || "Agent 流式响应失败");
          },
        },
        controller.signal
      );
    } catch (err: any) {
      if (err?.name !== "AbortError") setError(err.message || "Agent 请求失败");
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
      if (conversationId) void refreshTree();
    }
  };

  const clearChat = () => {
    abortRef.current?.abort();
    setConversationId(null);
    setResolvedProposalIds(new Set());
    setConfirmationChallenges({});
    setTree(null);
    setTreeOpen(false);
    setAgentState(initialState());
    setError("");
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
        if (result.continuation?.conversation_id) setConversationId(result.continuation.conversation_id);
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

  const refreshTree = async (): Promise<AgentConversationTree | null> => {
    if (!conversationId) return null;
    setTreeLoading(true);
    try {
      const nextTree = await harnessAgentApi.getConversationTree(conversationId);
      setTree(nextTree);
      return nextTree;
    } catch (err: any) {
      setError(err.message || "加载会话树失败");
      return null;
    } finally {
      setTreeLoading(false);
    }
  };

  const toggleTree = async () => {
    const nextOpen = !treeOpen;
    setTreeOpen(nextOpen);
    if (nextOpen) await refreshTree();
  };

  const navigateTree = async (entryId: string) => {
    if (!conversationId || loading) return;
    setTreeLoading(true);
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
    } finally {
      setTreeLoading(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-[calc(100vh-7rem)] max-w-6xl flex-col gap-4 pb-6">
      <section className="bauhaus-panel overflow-hidden bg-white">
        <div className="flex flex-col gap-5 p-5 md:p-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="bauhaus-panel-sm flex h-12 w-12 items-center justify-center bg-[#e4c46a] text-black">
                  <Bot size={22} />
                </div>
                <div>
                  <p className="bauhaus-label text-black/55">全局助手工作台</p>
                  <h1 className="mt-1 text-3xl font-black text-black md:text-4xl">OfferU 全局助手</h1>
                </div>
              </div>
              <p className="max-w-3xl text-sm font-medium leading-relaxed text-black/72 md:text-base">
                让助手读取你的求职上下文，按新 Agent 流式契约逐 token 响应。写操作会先生成 proposal，由你确认后再续跑。
              </p>
            </div>

            <div className="flex flex-col gap-2 md:items-end">
              <Chip className="w-fit border-2 border-black bg-[var(--surface-muted)] text-xs font-semibold text-black">
                {latestMode}
              </Chip>
              <div className="flex gap-2">
                <Button
                  variant="light"
                  onPress={toggleTree}
                  title="会话树"
                  startContent={<GitBranch size={16} />}
                  isDisabled={!conversationId}
                  className="bauhaus-button bauhaus-button-outline !justify-center !px-4 !py-3 !text-[11px]"
                >
                  树导航
                </Button>
                <Button
                  variant="light"
                  onPress={clearChat}
                  title="清空对话"
                  startContent={<Trash2 size={16} />}
                  className="bauhaus-button bauhaus-button-outline !justify-center !px-4 !py-3 !text-[11px]"
                >
                  重置
                </Button>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {QUICK_ACTIONS.map((action, index) => (
              <Chip
                key={action.label}
                className={`cursor-pointer border-2 border-black px-3 py-2 text-sm font-semibold ${
                  index % 3 === 0 ? "bg-[#e4c46a] text-black" : index % 3 === 1 ? "bg-white text-black" : "bg-[#f7ece9] text-black"
                }`}
                onClick={() => void sendMessage(action.prompt)}
              >
                {action.label}
              </Chip>
            ))}
          </div>
        </div>
      </section>

      {treeOpen && (
        <section className="bauhaus-panel-sm bg-white p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-sm font-black text-black">会话树</p>
            {treeLoading && <Loader2 size={14} className="animate-spin text-black/55" />}
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {(tree?.entries || []).map((entry) => (
              <button
                key={entry.entry_id}
                type="button"
                onClick={() => void navigateTree(entry.entry_id)}
                className={`border px-3 py-2 text-left text-xs transition-colors ${
                  entry.entry_id === tree?.leaf_id ? "border-black bg-[#FFF4D8]" : "border-black/20 bg-white hover:border-black"
                }`}
              >
                <p className="font-black text-black">{entry.entry_type}</p>
                <p className="mt-1 break-words font-medium text-black/60">{entry.preview}</p>
              </button>
            ))}
          </div>
        </section>
      )}

      <ScrollShadow className="bauhaus-panel min-h-[22rem] flex-1 overflow-y-auto bg-white p-4 md:min-h-[26rem] md:p-6">
        <div className="space-y-5">
          {agentState.messages.map((message) => (
            <AgentStreamMessageBubble key={message.id} message={message} />
          ))}
          {agentState.streaming && <StreamingAssistantBubble streaming={agentState.streaming} />}
          {loading && !agentState.streaming && (
            <div className="inline-flex items-center gap-2 border-2 border-black bg-white px-4 py-3 text-[15px] font-medium text-black/65 shadow-[2px_2px_0_0_rgba(18,18,18,0.3)]">
              <Loader2 size={14} className="animate-spin" />
              正在连接 Agent...
            </div>
          )}
          <ToolExecutionList executions={agentState.toolExecutions} />
          <AgentCardList cards={agentState.cards} />
        </div>
      </ScrollShadow>

      <PlanExecutionList plans={agentState.plans} />

      <ProposalList
        proposals={agentState.proposals}
        resolvedIds={resolvedProposalIds}
        loading={loading}
        onConfirm={(id) => void confirmProposal(id)}
        onReject={(id) => void rejectProposal(id)}
      />

      <ManualReviewCaseList
        sessionId={conversationId}
        cases={agentState.manualReviewCases}
        onCasesLoaded={(cases) => setAgentState((prev) => mergeManualReviewCases(prev, cases))}
        onResolution={(result) => setAgentState((prev) => applyManualReviewResolutionResponse(prev, result))}
      />
      {error && <div className="bauhaus-panel-sm bg-[#c95548] px-4 py-3 text-sm font-medium text-white">{error}</div>}

      <div className="bauhaus-panel-sm bg-white p-4 md:p-5">
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onValueChange={setInput}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void sendMessage();
              }
            }}
            placeholder="输入你的目标..."
            minRows={1}
            maxRows={4}
            variant="bordered"
            className="flex-1"
            classNames={bauhausFieldClassNames}
            isDisabled={loading}
          />
          <Button
            isIconOnly
            onPress={() => void sendMessage()}
            isDisabled={!input.trim() || loading}
            aria-label="发送消息"
            className="bauhaus-button bauhaus-button-red !mb-[2px] !min-h-11 !min-w-11 !px-0 !py-0"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
          </Button>
        </div>
      </div>
    </div>
  );
}
