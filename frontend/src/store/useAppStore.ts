import { create } from "zustand"
import { persist } from "zustand/middleware"
import type { AppState, ChatMessage, TimelineStep } from "@/types"
import { TIMELINE_STEPS } from "@/types"

function makeId() {
  return Math.random().toString(36).slice(2, 10)
}

function initialTimeline(): TimelineStep[] {
  return TIMELINE_STEPS.map((s) => ({ ...s, status: "pending" }))
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      threadId: null,
      effort: "medium",
      models: [],
      availableModels: [],
      selectedModel: "",
      messages: [],
      timeline: initialTimeline(),
      isStreaming: false,
      awaitingPlanConfirm: false,
      planContent: null,
      finalReport: null,
      finalReportSources: [],

      setThreadId: (id) => set({ threadId: id }),
      setEffort: (e) => set({ effort: e }),
      setModels: (m) => set({ models: m }),
      setAvailableModels: (m) => set({ availableModels: m }),
      setSelectedModel: (m) => set({ selectedModel: m }),

      addMessage: (msg) => {
        const newMsg: ChatMessage = {
          id: makeId(),
          timestamp: Date.now(),
          ...msg,
        }
        set((s) => ({ messages: [...s.messages, newMsg] }))
      },

      updateLastAssistant: (chunk) => {
        set((s) => {
          const msgs = [...s.messages]
          const lastIdx = msgs.length - 1
          if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
            msgs[lastIdx] = {
              ...msgs[lastIdx],
              content: msgs[lastIdx].content + chunk,
            }
          } else {
            msgs.push({
              id: makeId(),
              role: "assistant",
              content: chunk,
              timestamp: Date.now(),
              kind: "intermediate",
            })
          }
          return { messages: msgs }
        })
      },

      appendTokenToKind: (kind, chunk) => {
        set((s) => {
          const msgs = [...s.messages]
          const lastIdx = msgs.length - 1
          if (lastIdx >= 0 && msgs[lastIdx].role === "assistant" && msgs[lastIdx].kind === kind) {
            msgs[lastIdx] = { ...msgs[lastIdx], content: msgs[lastIdx].content + chunk }
          } else {
            msgs.push({
              id: makeId(),
              role: "assistant",
              content: chunk,
              timestamp: Date.now(),
              kind,
            })
          }
          return { messages: msgs }
        })
      },

      updateLastAssistantKind: (kind) => {
        set((s) => {
          const msgs = [...s.messages]
          const lastIdx = msgs.length - 1
          if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
            msgs[lastIdx] = { ...msgs[lastIdx], kind }
          }
          return { messages: msgs }
        })
      },

      replaceLastMessageOfKind: (kind, content) => {
        set((s) => {
          const msgs = [...s.messages]
          for (let i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].role === "assistant" && msgs[i].kind === kind) {
              msgs[i] = { ...msgs[i], content }
              return { messages: msgs }
            }
          }
          msgs.push({
            id: makeId(),
            role: "assistant",
            content,
            timestamp: Date.now(),
            kind,
          })
          return { messages: msgs }
        })
      },

      updateTimeline: (stepId, status, detail) => {
        set((s) => ({
          timeline: s.timeline.map((t) =>
            t.id === stepId
              ? { ...t, status, detail: detail ?? t.detail, timestamp: Date.now() }
              : t
          ),
        }))
      },

      setStreaming: (s) => set({ isStreaming: s }),

      setAwaitingPlanConfirm: (v, planContent) =>
        set({
          awaitingPlanConfirm: v,
          planContent: v ? planContent ?? get().planContent : null,
        }),

      setFinalReport: (report, sources) =>
        set({
          finalReport: report,
          finalReportSources: sources ?? [],
        }),

      reset: () =>
        set({
          threadId: null,
          messages: [],
          timeline: initialTimeline(),
          isStreaming: false,
          awaitingPlanConfirm: false,
          planContent: null,
          finalReport: null,
          finalReportSources: [],
        }),

      startNewSession: () => {
        get().reset()
      },
    }),
    {
      name: "deepresearch-session",
      // 只持久化会话相关字段；isStreaming / awaitingPlanConfirm 等瞬态标志刷新后回到初始值，
      // 避免恢复成"卡在生成中 / 等待确认"的假状态。
      partialize: (state) => ({
        threadId: state.threadId,
        effort: state.effort,
        selectedModel: state.selectedModel,
        messages: state.messages,
        timeline: state.timeline,
        finalReport: state.finalReport,
        finalReportSources: state.finalReportSources,
      }),
    },
  ),
)
