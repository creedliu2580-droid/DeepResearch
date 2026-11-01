import { useEffect, useState } from "react"
import { History, Loader2 } from "lucide-react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

interface Session {
  thread_id: string
  title: string
  updated_at: number
}

interface Props {
  open: boolean
  onOpenChange: (v: boolean) => void
  onSelect: (threadId: string) => void
}

export function SessionList({ open, onOpenChange, onSelect }: Props) {
  const [sessions, setSessions] = useState<Session[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    fetch("/api/sessions")
      .then((r) => r.json())
      .then((d) => setSessions(d.sessions ?? []))
      .catch(() => setSessions([]))
      .finally(() => setLoading(false))
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <History className="h-4 w-4 text-neutral-500" />
            历史会话
          </DialogTitle>
        </DialogHeader>

        <div className="max-h-[60vh] overflow-y-auto">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-neutral-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              加载中…
            </div>
          ) : sessions.length === 0 ? (
            <p className="py-8 text-center text-sm text-neutral-400">暂无历史会话</p>
          ) : (
            <ul className="space-y-1">
              {sessions.map((s) => (
                <li key={s.thread_id}>
                  <button
                    type="button"
                    onClick={() => {
                      onSelect(s.thread_id)
                      onOpenChange(false)
                    }}
                    className="w-full rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-neutral-100"
                  >
                    <span className="line-clamp-1 font-medium text-neutral-800">
                      {s.title || "（无标题）"}
                    </span>
                    <span className="text-xs text-neutral-400">
                      {new Date(s.updated_at * 1000).toLocaleString()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
