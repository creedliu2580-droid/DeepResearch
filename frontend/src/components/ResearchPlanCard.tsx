import { useState } from "react"
import { CheckCircle2, Pen, Send } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

interface Props {
  onConfirm: () => void
  onModify: (feedback: string) => void
  disabled?: boolean
}

export function ResearchPlanCard({ onConfirm, onModify, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const [feedback, setFeedback] = useState("")

  const handleSubmit = () => {
    const trimmed = feedback.trim()
    if (trimmed) {
      onModify(trimmed)
      setFeedback("")
      setOpen(false)
    }
  }

  const handleOpenChange = (v: boolean) => {
    setOpen(v)
    if (!v) setFeedback("")
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        onClick={onConfirm}
        disabled={disabled}
        className="h-9 rounded-2xl bg-blue-600 px-5 text-sm font-medium text-white shadow-none hover:bg-blue-500"
      >
        <CheckCircle2 className="h-4 w-4" />
        开始研究
      </Button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogTrigger asChild>
          <Button
            variant="ghost"
            disabled={disabled}
            className="h-9 rounded-2xl border border-neutral-200 bg-white px-4 text-sm text-neutral-600 shadow-sm hover:bg-neutral-100 hover:text-neutral-900"
          >
            <Pen className="h-4 w-4" />
            修改计划
          </Button>
        </DialogTrigger>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>修改研究计划</DialogTitle>
            <DialogDescription>
              告诉我需要调整什么方向、增减哪些内容，我会重新生成研究计划。
            </DialogDescription>
          </DialogHeader>

          <Textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="例如：聚焦在开源工具，去掉商业产品部分；增加 2024 年后的案例..."
            className="min-h-[140px] resize-y rounded-xl border-neutral-200 bg-neutral-50 text-sm leading-6 focus:bg-white"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") handleSubmit()
            }}
          />

          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => handleOpenChange(false)}
              className="rounded-xl"
            >
              取消
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={!feedback.trim()}
              className="rounded-xl bg-blue-600 text-white hover:bg-blue-500"
            >
              <Send className="h-4 w-4" />
              提交修改
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
