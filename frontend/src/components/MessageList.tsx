import { useEffect, useRef, useState } from "react"
import { ChevronDown, ChevronUp, Loader2 } from "lucide-react"
import { useAppStore } from "@/store/useAppStore"
import { MarkdownView } from "@/components/MarkdownView"
import { CopyButton } from "@/components/CopyButton"
import { ResearchPlanCard } from "@/components/ResearchPlanCard"
import { ReportActions } from "@/components/ReportActions"
import { Timeline } from "@/components/Timeline"

interface Props {
  onConfirmPlan: () => void
  onModifyPlan: (feedback: string) => void
}

/**
 * 消息折叠规则：
 * - kind === "report"   → 始终展开（最终报告）
 * - kind === "answer"   → 始终展开（QA 回答）
 * - kind === "error"    → 始终展开
 * - kind === "plan"     → 确认前展开、确认后自动收起
 * - kind === "intermediate" → 流式中展开、完成后自动收起
 * - 无 kind 或 role === "user" → 始终展开
 */
function shouldCollapseByDefault(kind?: string, isStreaming?: boolean, awaitingPlanConfirm?: boolean): boolean {
  if (kind === "report" || kind === "answer" || kind === "error" || !kind) return false
  if (kind === "plan" && awaitingPlanConfirm) return false
  if (kind === "intermediate" && isStreaming) return false
  if (kind === "plan" && !awaitingPlanConfirm) return true
  if (kind === "intermediate" && !isStreaming) return true
  return false
}

const KIND_LABEL: Record<string, string> = {
  plan: "研究计划",
  intermediate: "研究过程",
}

/** 去掉 LLM 输出里的「（上限 X 字）」「（上限 X 字以内）」等字数标注 */
function stripCharLimit(text: string): string {
  return text.replace(/[（(]\s*上限\s*[\d,]+\s*字(?:以内)?\s*[）)]/g, "")
}

export function MessageList({ onConfirmPlan, onModifyPlan }: Props) {
  const messages = useAppStore((s) => s.messages)
  const isStreaming = useAppStore((s) => s.isStreaming)
  const awaitingPlanConfirm = useAppStore((s) => s.awaitingPlanConfirm)
  const planContent = useAppStore((s) => s.planContent)
  const finalReport = useAppStore((s) => s.finalReport)
  const finalReportSources = useAppStore((s) => s.finalReportSources)
  const threadId = useAppStore((s) => s.threadId)
  const timeline = useAppStore((s) => s.timeline)
  // 研究过程卡片：plan 之后的步骤一旦开始就展示（挂在最后一条研究计划消息下方）
  const progressStarted = timeline.some((t) => t.id !== "plan" && t.status !== "pending")
  const lastPlanMsgId = [...messages].reverse().find((m) => m.role === "assistant" && m.kind === "plan")?.id
  const listRef = useRef<HTMLDivElement>(null)
  // 每条消息的手动折叠状态（覆盖默认行为）
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set())

  // 自动计算每条消息的默认折叠状态 — 当 isStreaming / awaitingPlanConfirm 变化时重新计算
  useEffect(() => {
    const next = new Set<string>()
    messages.forEach((m) => {
      if (m.role === "assistant" && shouldCollapseByDefault(m.kind, isStreaming, awaitingPlanConfirm)) {
        next.add(m.id)
      }
    })
    // 保留用户手动切换过的状态，只对新增/消失的消息生效
    setCollapsedIds((prev) => {
      // 先把已经不存在的 id 清掉
      messages.forEach((m) => {
        if (!next.has(m.id) && !prev.has(m.id)) {
          // 默认展开且用户没手动折叠过 → 保持展开
        }
      })
      // 对应该自动收起的新消息 → 收起（除非用户之前手动展开过它，这种情况很少见，简单处理）
      return next
    })
  }, [isStreaming, awaitingPlanConfirm, messages.length])

  const lastMessage = messages[messages.length - 1]

  // 自动滚到底
  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, lastMessage?.content, isStreaming])

  const toggleCollapse = (id: string) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div ref={listRef} className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-5 px-4 pb-6 pt-6">
        {messages.map((msg, index) => {
          const isLast = index === messages.length - 1

          // 用户消息 —— 右对齐气泡，始终展开
          if (msg.role === "user") {
            return (
              <div key={msg.id} className="flex justify-end">
                <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl rounded-br-lg border border-neutral-200 bg-neutral-100 px-4 py-2.5 text-sm leading-7 text-neutral-900">
                  {msg.content}
                </div>
              </div>
            )
          }

          // 中间过程消息不再渲染（进展统一收进「研究过程」卡片）
          if (msg.kind === "intermediate") return null

          // 助手消息
          const kind = msg.kind
          const showPlan = awaitingPlanConfirm && isLast && kind === "plan"
          const isFinalReport = kind === "report" || (Boolean(finalReport) && msg.content === finalReport)
          // 最终报告优先用解析后的终稿（真实链接），而不是流式阶段的 [N] 编号原文
          const rawContent = showPlan && planContent ? planContent : (isFinalReport && finalReport ? finalReport : msg.content)
          const content = stripCharLimit(rawContent)
          const collapsed = collapsedIds.has(msg.id)

          const collapsible = kind === "plan"

          return (
            <div key={msg.id} className="flex animate-fade-in-up flex-col gap-2">
              {/* 折叠控制条（plan / intermediate 才有） */}
              {collapsible && content && (
                <button
                  type="button"
                  onClick={() => toggleCollapse(msg.id)}
                  className="flex w-fit items-center gap-1.5 rounded-full border border-neutral-200 bg-white px-3 py-1 text-xs text-neutral-500 transition-colors hover:bg-neutral-100"
                >
                  {collapsed ? (
                    <>
                      <ChevronDown className="h-3 w-3" />
                      展开{KIND_LABEL[kind ?? ""] ?? "详情"}
                    </>
                  ) : (
                    <>
                      <ChevronUp className="h-3 w-3" />
                      收起{KIND_LABEL[kind ?? ""] ?? "详情"}
                    </>
                  )}
                </button>
              )}

              {/* 助手回复 —— 用气泡框框住，类似用户消息 */}
              {(Boolean(content) || (isLast && isStreaming)) && (
                <div className="flex justify-start">
                  <div className="max-w-[95%] rounded-3xl rounded-bl-lg border border-neutral-200 bg-white px-5 py-3.5 shadow-sm">
                    {content && !collapsed ? (
                      <MarkdownView
                        content={content}
                        sources={isFinalReport ? finalReportSources : undefined}
                      />
                    ) : content && collapsed ? (
                      <p className="text-xs text-neutral-400 italic">
                        （已折叠，点击上方按钮展开）
                      </p>
                    ) : (
                      <div className="flex items-center gap-2 text-sm text-neutral-500">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        处理中…
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* 研究计划卡片（确认前显示） */}
              {showPlan && (
                <ResearchPlanCard
                  onConfirm={onConfirmPlan}
                  onModify={onModifyPlan}
                  disabled={isStreaming}
                />
              )}

              {/* 研究过程大折叠卡：跟在最后一条研究计划消息后面，展开看每一步进展 */}
              {msg.id === lastPlanMsgId && progressStarted && (
                <Timeline live={isStreaming} variant="embedded" />
              )}

              {/* 最终报告：底部放复制 + 下载 + 查看按钮 */}
              {isFinalReport && content && !collapsed && (
                <ReportActions
                  content={content}
                  threadId={threadId}
                  sources={finalReportSources}
                />
              )}

              {/* 非折叠消息的复制按钮 */}
              {!showPlan && !isFinalReport && content && (kind === "answer" || kind === "error" || isLast) && !collapsed && (
                <div className="flex justify-end">
                  <CopyButton content={content} />
                </div>
              )}
            </div>
          )
        })}

        {isStreaming && lastMessage?.role === "user" && (
          <div className="flex items-center gap-2 text-sm text-neutral-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            处理中…
          </div>
        )}
      </div>
    </div>
  )
}
