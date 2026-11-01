export type EffortLevel = "low" | "medium" | "high"

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  timestamp: number
  // kind 控制折叠行为：
  // - plan: 研究计划（确认前显示，确认后自动折叠）
  // - intermediate: 研究过程中间输出（node_end 后折叠）
  // - report: 最终报告（始终展开）
  // - answer: 报告问答回答（展开）
  // - error: 错误提示（展开）
  kind?: "plan" | "intermediate" | "report" | "answer" | "error"
}

export interface TimelineStep {
  id: string
  label: string
  status: "pending" | "running" | "done" | "skipped"
  timestamp?: number
  detail?: string
}

export interface AppState {
  // 会话
  threadId: string | null
  effort: EffortLevel
  models: { role: string; model: string }[]
  availableModels: string[]
  selectedModel: string

  // 消息
  messages: ChatMessage[]

  // 时间线
  timeline: TimelineStep[]

  // 控制
  isStreaming: boolean
  awaitingPlanConfirm: boolean
  planContent: string | null
  finalReport: string | null
  finalReportSources: { title: string; url: string; query?: string }[]

  // actions
  setThreadId: (id: string) => void
  setEffort: (e: EffortLevel) => void
  setModels: (m: { role: string; model: string }[]) => void
  setAvailableModels: (m: string[]) => void
  setSelectedModel: (m: string) => void
  addMessage: (msg: Omit<ChatMessage, "id" | "timestamp">) => void
  updateLastAssistant: (chunk: string) => void
  /** 把 token 追加到「最后一条且 kind 匹配」的 assistant 消息；不匹配则新建一条该 kind 的消息 */
  appendTokenToKind: (kind: ChatMessage["kind"], chunk: string) => void
  updateLastAssistantKind: (kind: ChatMessage["kind"]) => void
  /** 用最终结果覆盖「最后一条且 kind 匹配」的 assistant 消息内容（用于把流式原始文本替换为解析后的终稿） */
  replaceLastMessageOfKind: (kind: ChatMessage["kind"], content: string) => void
  updateTimeline: (stepId: string, status: TimelineStep["status"], detail?: string) => void
  setStreaming: (s: boolean) => void
  setAwaitingPlanConfirm: (v: boolean, planContent?: string) => void
  setFinalReport: (report: string, sources?: { title: string; url: string; query?: string }[]) => void
  reset: () => void
  startNewSession: () => void
}

export const TIMELINE_STEPS: { id: string; label: string }[] = [
  { id: "plan", label: "生成研究计划" },
  { id: "split_queries", label: "拆分搜索查询" },
  { id: "search", label: "网络深度研究" },
  { id: "reflect", label: "反思与补搜" },
  { id: "outline", label: "撰写报告大纲" },
  { id: "draft", label: "撰写草稿" },
  { id: "review", label: "审稿与评分" },
  { id: "polish", label: "最终润色" },
]
