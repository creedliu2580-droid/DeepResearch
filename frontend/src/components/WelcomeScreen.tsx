import { useAppStore } from "@/store/useAppStore"
import { ChatInput, EFFORT_OPTIONS } from "@/components/ChatInput"

interface Props {
  onSubmit: (input: string) => void
  onOpenHistory?: () => void
  onCancel?: () => void
}

export function WelcomeScreen({ onSubmit, onOpenHistory, onCancel }: Props) {
  const effort = useAppStore((s) => s.effort)
  const current = EFFORT_OPTIONS.find((o) => o.value === effort) ?? EFFORT_OPTIONS[1]

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-1 flex-col items-center justify-center gap-4 px-4 text-center">
      <div>
        <h1 className="mb-3 text-4xl font-semibold text-neutral-900 md:text-6xl">
          欢迎你！深度研究助手
        </h1>
        <p className="text-lg text-neutral-500 md:text-2xl">
          写下你的研究主题，我会先梳理计划、再联网检索，最后产出带引用的研究报告
        </p>
      </div>

      <div className="mt-4 w-full">
        <ChatInput onSubmit={onSubmit} onCancel={onCancel} onOpenHistory={onOpenHistory} />
      </div>

      <p className="text-xs text-neutral-500">
        DeepResearch · 基于 LLM 的深度搜索（研究深度：
        {current.label}，{current.desc}）
      </p>
    </div>
  )
}
