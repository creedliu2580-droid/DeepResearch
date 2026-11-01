import { useEffect, useState } from "react"
import { CheckCircle2, Loader2, ChevronDown, ChevronUp } from "lucide-react"
import { useAppStore } from "@/store/useAppStore"
import type { TimelineStep } from "@/types"
import { cn } from "@/lib/utils"

const STATUS_TEXT: Record<string, string> = {
  done: "已完成",
  running: "进行中",
  skipped: "已跳过",
  pending: "待开始",
}

interface GroupDef {
  id: string
  label: string
  stepIds: string[]
}

const GROUPS: GroupDef[] = [
  { id: "plan", label: "生成研究计划", stepIds: ["plan"] },
  { id: "search", label: "网络深度搜索", stepIds: ["split_queries", "search", "reflect"] },
  { id: "outline", label: "撰写报告大纲", stepIds: ["outline"] },
  { id: "writing", label: "报告生成", stepIds: ["draft", "review", "polish"] },
]

type GroupWithSteps = GroupDef & { steps: TimelineStep[] }
type GroupStatus = "pending" | "running" | "done"

function groupStatusOf(steps: TimelineStep[]): GroupStatus {
  if (steps.length > 0 && steps.every((s) => s.status === "done")) return "done"
  if (steps.some((s) => s.status === "running" || s.status === "done")) return "running"
  return "pending"
}

function GroupStatusIcon({ status }: { status: GroupStatus }) {
  if (status === "running") return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
  if (status === "done") return <CheckCircle2 className="h-4 w-4 text-green-500" />
  return <div className="h-4 w-4 rounded-full border-2 border-neutral-300" />
}

interface Props {
  live: boolean
  variant?: "embedded" | "sidebar"
}

export function Timeline({ live, variant = "sidebar" }: Props) {
  const timeline = useAppStore((s) => s.timeline)
  const stepById = new Map(timeline.map((t) => [t.id, t]))
  const groups: GroupWithSteps[] = GROUPS.map((g) => ({
    ...g,
    steps: g.stepIds
      .map((id) => stepById.get(id))
      .filter((s): s is TimelineStep => Boolean(s)),
  }))

  // 侧栏：只平铺 4 个模块，无折叠
  if (variant === "sidebar") {
    return (
      <div className="flex flex-col gap-1">
        <h3 className="mb-2 flex items-center gap-2 text-sm font-medium text-neutral-700">
          {live ? (
            <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-neutral-500" />
          )}
          研究进度
        </h3>
        <div className="space-y-1">
          {groups.map((g) => {
            const status = groupStatusOf(g.steps)
            const runningStep = g.steps.find((s) => s.status === "running")
            return (
              <div
                key={g.id}
                className={cn(
                  "flex items-start gap-2.5 rounded-lg px-2 py-2",
                  status === "running" && "bg-blue-50/60",
                )}
              >
                <div className="mt-0.5 flex-shrink-0">
                  <GroupStatusIcon status={status} />
                </div>
                <div className="min-w-0">
                  <p
                    className={cn(
                      "text-sm leading-tight",
                      status === "running" && "font-medium text-blue-700",
                      status === "done" && "text-neutral-800",
                      status === "pending" && "text-neutral-400",
                    )}
                  >
                    {g.label}
                  </p>
                  {runningStep && (
                    <p className="mt-0.5 truncate text-xs text-neutral-400">
                      {runningStep.label}…
                    </p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  // 主页：大折叠卡，展开后看每一步进展
  return <EmbeddedTimeline live={live} groups={groups} />
}

function EmbeddedTimeline({ live, groups }: { live: boolean; groups: GroupWithSteps[] }) {
  // 运行中默认展开，结束后默认收起；用户可随时手动切换（按钮始终保留）
  const [collapsed, setCollapsed] = useState(!live)
  useEffect(() => {
    setCollapsed(!live)
  }, [live])

  const totalSteps = groups.reduce((n, g) => n + g.steps.length, 0)
  const doneSteps = groups.reduce(
    (n, g) => n + g.steps.filter((s) => s.status === "done").length,
    0,
  )

  return (
    <div className="overflow-hidden rounded-2xl border border-neutral-200 bg-neutral-50">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-sm text-neutral-800 transition-colors hover:bg-neutral-100"
      >
        <span className="flex items-center gap-2">
          {live ? (
            <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-green-500" />
          )}
          {live ? "研究进行中" : "研究过程"}
          <span className="text-xs font-normal text-neutral-400">
            {doneSteps}/{totalSteps} 步
          </span>
        </span>
        {collapsed ? (
          <ChevronDown className="h-4 w-4 text-neutral-500" />
        ) : (
          <ChevronUp className="h-4 w-4 text-neutral-500" />
        )}
      </button>

      {!collapsed && (
        <div className="space-y-3 px-4 pb-4">
          {groups.map((g) => {
            const status = groupStatusOf(g.steps)
            return (
              <div key={g.id}>
                <div className="flex items-center gap-2">
                  <GroupStatusIcon status={status} />
                  <span
                    className={cn(
                      "text-sm font-medium",
                      status === "running" && "text-blue-700",
                      status === "done" && "text-neutral-800",
                      status === "pending" && "text-neutral-400",
                    )}
                  >
                    {g.label}
                  </span>
                </div>
                <div className="ml-6 mt-1 space-y-1.5 border-l border-neutral-200 pl-3">
                  {g.steps.map((step) => {
                    const running = step.status === "running"
                    return (
                      <div key={step.id}>
                        <p
                          className={cn(
                            "text-xs",
                            running && "font-medium text-blue-600",
                            step.status === "done" && "text-neutral-600",
                            step.status === "pending" && "text-neutral-400",
                          )}
                        >
                          {step.label}
                          <span className="ml-1.5 text-[10px] font-normal text-neutral-400">
                            {STATUS_TEXT[step.status] ?? ""}
                          </span>
                          {running && (
                            <Loader2 className="ml-1 inline h-3 w-3 animate-spin text-blue-500" />
                          )}
                        </p>
                        {step.detail && (
                          <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-neutral-400">
                            {step.detail}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
