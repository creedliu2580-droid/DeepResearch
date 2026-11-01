import { useState } from "react"
import { Brain, ChevronDown, Cpu, History, Send, SquarePen, StopCircle } from "lucide-react"
import { useAppStore } from "@/store/useAppStore"
import { cn } from "@/lib/utils"
import type { EffortLevel } from "@/types"

interface Props {
  onSubmit: (input: string) => void
  onQASubmit?: (q: string) => void
  onNewSession?: () => void
  onOpenHistory?: () => void
  onCancel?: () => void
}

export const EFFORT_OPTIONS: { value: EffortLevel; label: string; desc: string }[] = [
  { value: "low", label: "快速", desc: "1 组查询" },
  { value: "medium", label: "均衡", desc: "3 组查询" },
  { value: "high", label: "全面", desc: "5 组查询" },
]

export function ChatInput({
  onSubmit,
  onQASubmit,
  onNewSession,
  onOpenHistory,
  onCancel,
}: Props) {
  const [value, setValue] = useState("")
  const effort = useAppStore((s) => s.effort)
  const setEffort = useAppStore((s) => s.setEffort)
  const isStreaming = useAppStore((s) => s.isStreaming)
  const finalReport = useAppStore((s) => s.finalReport)
  const models = useAppStore((s) => s.models)
  const availableModels = useAppStore((s) => s.availableModels)
  const selectedModel = useAppStore((s) => s.selectedModel)
  const setSelectedModel = useAppStore((s) => s.setSelectedModel)

  const qaMode = Boolean(finalReport)
  const modelLabel =
    Array.from(new Set(models.map((m) => m.model))).join(" / ") || "默认模型"
  const disabled = !value.trim() || isStreaming

  const submit = () => {
    const text = value.trim()
    if (!text || isStreaming) return
    if (qaMode) onQASubmit?.(text)
    else onSubmit(text)
    setValue("")
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
      className="flex shrink-0 flex-col gap-2 p-3 pb-4"
    >
      {/* 输入容器 */}
      <div className="flex items-end gap-2 rounded-3xl rounded-bl-sm border border-neutral-200 bg-white px-4 py-2.5 shadow-sm transition-shadow focus-within:border-neutral-300 focus-within:ring-1 focus-within:ring-neutral-300">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            qaMode
              ? "基于当前报告继续追问…"
              : "写下一个研究主题，例如：分析 AI 编程工具的市场格局"
          }
          rows={1}
          className="max-h-[200px] min-h-[36px] w-full resize-none border-0 bg-transparent py-1.5 text-base leading-7 text-neutral-900 placeholder-neutral-400 shadow-none outline-none focus:outline-none focus:ring-0"
          disabled={isStreaming}
        />
        {isStreaming ? (
          <button
            type="button"
            onClick={onCancel}
            className="mb-0.5 flex shrink-0 items-center gap-1 rounded-full p-2 text-red-500 transition-colors hover:bg-red-50 hover:text-red-600"
            title="停止生成"
          >
            <StopCircle className="h-5 w-5" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={disabled}
            className={cn(
              "mb-0.5 flex shrink-0 items-center gap-1 rounded-full px-3 py-1.5 text-sm transition-colors",
              disabled
                ? "cursor-not-allowed text-neutral-400"
                : "text-blue-600 hover:bg-blue-50 hover:text-blue-700",
            )}
          >
            {qaMode ? "追问" : "探索"}
            <Send className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* 参数选择区 */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {!qaMode && (
            <div className="flex items-center gap-2 rounded-xl rounded-t-sm border border-neutral-200 bg-white py-1.5 pl-3 pr-2 text-sm text-neutral-600 shadow-sm">
              <Brain className="h-4 w-4 text-neutral-400" />
              <span className="hidden text-neutral-500 sm:inline">研究深度</span>
              <div className="relative">
                <select
                  value={effort}
                  onChange={(e) => setEffort(e.target.value as EffortLevel)}
                  disabled={isStreaming}
                  title="研究深度决定搜索查询数量与反思轮次"
                  className="cursor-pointer appearance-none bg-transparent pr-5 text-sm text-neutral-800 outline-none disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {EFFORT_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value} className="bg-white">
                      {o.label}（{o.desc}）
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-0 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
              </div>
            </div>
          )}
          <div className="flex items-center gap-2 rounded-xl rounded-t-sm border border-neutral-200 bg-white px-3 py-1.5 text-sm shadow-sm">
            <Cpu className="h-4 w-4 text-neutral-400" />
            <span className="hidden text-neutral-500 sm:inline">模型</span>
            {availableModels.length > 0 ? (
              <div className="relative">
                <select
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  disabled={isStreaming || qaMode}
                  title={
                    qaMode
                      ? "当前会话已锁定创建时的模型"
                      : "计划、搜索、写作、审稿四个阶段统一使用该模型"
                  }
                  className="max-w-[220px] cursor-pointer appearance-none truncate bg-transparent pr-5 text-sm text-neutral-800 outline-none disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {availableModels.map((m) => (
                    <option key={m} value={m} className="bg-white">
                      {m}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-0 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-neutral-400" />
              </div>
            ) : (
              <span className="text-neutral-800">{modelLabel}</span>
            )}
          </div>

          {onOpenHistory && (
            <button
              type="button"
              onClick={onOpenHistory}
              className="flex items-center gap-1.5 rounded-xl rounded-t-sm border border-neutral-200 bg-white px-3 py-1.5 text-sm text-neutral-600 shadow-sm transition-colors hover:bg-neutral-100 hover:text-neutral-900"
            >
              <History className="h-4 w-4" />
              历史
            </button>
          )}
        </div>

        {onNewSession && (
          <button
            type="button"
            onClick={onNewSession}
            className="flex shrink-0 items-center gap-1.5 rounded-xl rounded-t-sm border border-neutral-200 bg-white px-3 py-2 text-sm text-neutral-600 shadow-sm transition-colors hover:bg-neutral-100 hover:text-neutral-900"
          >
            <SquarePen className="h-4 w-4" />
            探索新专题
          </button>
        )}
      </div>
    </form>
  )
}
