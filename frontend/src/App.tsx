import { useEffect, useRef, useState } from "react"
import { MessageList } from "@/components/MessageList"
import { ChatInput } from "@/components/ChatInput"
import { WelcomeScreen } from "@/components/WelcomeScreen"
import { SessionList } from "@/components/SessionList"
import { fetchModels, streamPost } from "@/api/client"
import { useAppStore } from "@/store/useAppStore"
import { TIMELINE_STEPS } from "@/types"

const NODE_TO_STEP: Record<string, string> = Object.fromEntries(
  TIMELINE_STEPS.map((s) => [s.id, s.id])
)
// 子图内部节点 → 顶层 step 映射（简化处理）
NODE_TO_STEP.split_queries = "split_queries"
NODE_TO_STEP.search = "search"
NODE_TO_STEP.reflect = "reflect"
NODE_TO_STEP.outline = "outline"
NODE_TO_STEP.draft = "draft"
NODE_TO_STEP.review = "review"
NODE_TO_STEP.polish = "polish"
// 注意：plan_confirm 只是等待确认，不映射为步骤，避免把已完成的「生成研究计划」重新置为进行中


export default function App() {
  const {
    threadId,
    messages,
    setThreadId,
    setModels,
    setAvailableModels,
    setSelectedModel,
    addMessage,
    updateLastAssistant,
    appendTokenToKind,
    updateLastAssistantKind,
    replaceLastMessageOfKind,
    updateTimeline,
    setStreaming,
    setAwaitingPlanConfirm,
    setFinalReport,
    startNewSession,
  } = useAppStore()

  const abortRef = useRef<AbortController | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)

  // 加载模型列表
  useEffect(() => {
    fetchModels().then(({ models, available }) => {
      setModels(models)
      setAvailableModels(available)
      if (!useAppStore.getState().selectedModel) {
        setSelectedModel(available[0] ?? "")
      }
    })
  }, [setModels, setAvailableModels, setSelectedModel])

  // 启动新会话 → POST /api/chat/start → 消费 SSE
  const handleNewSession = async (input?: string) => {
    startNewSession()
    if (!input) return

    const newThreadId = ""
    setThreadId(newThreadId)
    addMessage({ role: "user", content: input })
    setStreaming(true)

    const currentEffort = useAppStore.getState().effort

    abortRef.current = new AbortController()

    await streamPost(
      "/chat/start",
      {
        user_input: input,
        effort: currentEffort,
        model: useAppStore.getState().selectedModel,
      },
      {
        onEvent: (evt) => handleSSEEvent(evt, newThreadId),
        onError: (msg) => {
          setStreaming(false)
          addMessage({ role: "assistant", content: `⚠️ ${msg}`, kind: "error" })
        },
      },
      abortRef.current.signal,
    )

    setStreaming(false)
  }

  // 确认研究计划
  const handleConfirmPlan = async () => {
    if (!threadId) return
    setStreaming(true)
    setAwaitingPlanConfirm(false)

    abortRef.current = new AbortController()

    await streamPost(
      "/chat/resume",
      { thread_id: threadId, feedback: "开始研究", confirmed: true },
      {
        onEvent: (evt) => handleSSEEvent(evt, threadId),
        onError: (msg) => {
          setStreaming(false)
          addMessage({ role: "assistant", content: `⚠️ ${msg}`, kind: "error" })
        },
      },
      abortRef.current.signal,
    )
    setStreaming(false)
  }

  // 修改研究计划
  const handleModifyPlan = async (feedback: string) => {
    if (!threadId) return
    setStreaming(true)
    setAwaitingPlanConfirm(false)
    // 把用户提交的修改意见作为一条用户消息展示出来
    addMessage({ role: "user", content: feedback })
    // 预建新的 plan 消息，承接重新生成的计划 token 流
    addMessage({ role: "assistant", content: "", kind: "plan" })

    abortRef.current = new AbortController()

    await streamPost(
      "/chat/resume",
      { thread_id: threadId, feedback, confirmed: false },
      {
        onEvent: (evt) => handleSSEEvent(evt, threadId),
        onError: (msg) => {
          setStreaming(false)
          addMessage({ role: "assistant", content: `⚠️ ${msg}`, kind: "error" })
        },
      },
      abortRef.current.signal,
    )
    setStreaming(false)
  }

  // 报告问答
  const handleQA = async (question: string) => {
    if (!threadId) return
    setStreaming(true)
    addMessage({ role: "user", content: question })
    addMessage({ role: "assistant", content: "", kind: "answer" })

    abortRef.current = new AbortController()

    await streamPost(
      "/chat/qa",
      { thread_id: threadId, question },
      {
        onEvent: (evt) => handleSSEEvent(evt, threadId),
        onError: (msg) => {
          setStreaming(false)
          addMessage({ role: "assistant", content: `⚠️ ${msg}`, kind: "error" })
        },
      },
      abortRef.current.signal,
    )
    setStreaming(false)
  }

  // 停止当前生成
  const handleCancel = () => {
    abortRef.current?.abort()
    setStreaming(false)
  }

  // 加载历史会话
  const handleLoadSession = async (tid: string) => {
    try {
      const res = await fetch(`/api/sessions/${tid}`)
      if (!res.ok) return
      const data = await res.json()
      startNewSession()
      setThreadId(tid)
      if (data.report) {
        setFinalReport(data.report, data.sources ?? [])
        addMessage({ role: "assistant", content: data.report, kind: "report" })
      }
    } catch {
      // 忽略加载失败
    }
  }

  // 跟踪当前正在运行的节点 — 用于决定 token 流归属哪条消息
  const activeNodeRef = useRef<string | null>(null)

  // 统一处理 SSE 事件
  const handleSSEEvent = (evt: Record<string, unknown>, _tid: string) => {
    const type = evt.type as string

    switch (type) {
      case "session": {
        const tidFromServer = evt.thread_id as string
        if (tidFromServer) setThreadId(tidFromServer)
        break
      }
      case "node_start": {
        const node = evt.node as string
        activeNodeRef.current = node
        const stepId = NODE_TO_STEP[node]
        if (stepId) {
          updateTimeline(stepId, "running")
        }
        break
      }
      case "token": {
        const node = (evt.node as string) || activeNodeRef.current
        const content = (evt.content as string) ?? ""
        if (!content || !node) break
        // 正文只流式展示：需求澄清（plan）和最终报告（polish）；
        // 其余节点（搜索/大纲/草稿/审稿）只在研究过程卡片里反映状态
        if (node === "plan") appendTokenToKind("plan", content)
        else if (node === "polish") appendTokenToKind("report", content)
        else if (node === "qa") updateLastAssistant(content)
        break
      }
      case "node_end": {
        const node = evt.node as string
        const summary = (evt.summary as string) ?? ""
        const stepId = NODE_TO_STEP[node]
        if (stepId) {
          updateTimeline(stepId, "done", summary || undefined)
        }
        if (activeNodeRef.current === node) activeNodeRef.current = null
        break
      }
      case "interrupt": {
        const reason = evt.reason as string
        if (reason === "await_plan_confirm") {
          // plan 确认前：把最后一条 assistant 消息标记为 plan kind
          updateLastAssistantKind("plan")
          setAwaitingPlanConfirm(true)
        }
        break
      }
      case "report_done": {
        const report = evt.report as string
        const sources = (evt.sources as { title: string; url: string }[]) ?? []
        if (report) {
          setFinalReport(report, sources)
          // 用解析后的终稿覆盖流式阶段显示的原始文本（流式里是 [N] 编号，这里是真实链接）
          replaceLastMessageOfKind("report", report)
        }
        break
      }
      case "answer_done": {
        const answer = evt.answer as string
        if (answer) {
          // 用解析后的答案覆盖流式阶段的 [N] 原文（联网追问时这里是真实链接）
          replaceLastMessageOfKind("answer", answer)
        }
        break
      }
      case "error": {
        const msg = evt.message as string
        addMessage({ role: "assistant", content: `⚠️ ${msg}`, kind: "error" })
        break
      }
      case "done": {
        break
      }
    }
  }

  // Cleanup: 卸载时 abort
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  return (
    <div className="flex h-screen bg-neutral-50 font-sans text-neutral-900 antialiased">
      {/* 主区域 */}
      <main className="flex-1 flex min-w-0 flex-col">
        {messages.length === 0 ? (
          <WelcomeScreen
            onSubmit={(input) => handleNewSession(input)}
            onOpenHistory={() => setHistoryOpen(true)}
            onCancel={handleCancel}
          />
        ) : (
          <div className="mx-auto flex h-full w-full max-w-4xl flex-col">
            <MessageList
              onConfirmPlan={handleConfirmPlan}
              onModifyPlan={handleModifyPlan}
            />
            <ChatInput
              onSubmit={(input) => handleNewSession(input)}
              onQASubmit={handleQA}
              onNewSession={() => handleNewSession()}
              onOpenHistory={() => setHistoryOpen(true)}
              onCancel={handleCancel}
            />
          </div>
        )}
      </main>

      <SessionList
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        onSelect={handleLoadSession}
      />
    </div>
  )
}
