import { useState } from "react"
import { Copy, Eye, Download, Check, Loader2, FileText } from "lucide-react"
import { cn } from "@/lib/utils"
import { preprocessReportMarkdown } from "@/components/MarkdownView"

interface Source {
  title: string
  url: string
  query?: string
}

interface Props {
  content: string
  threadId: string | null
  sources?: Source[]
  className?: string
}

export function ReportActions({ content, threadId, sources = [], className }: Props) {
  const [copied, setCopied] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content)
    } catch {
      const ta = document.createElement("textarea")
      ta.value = content
      document.body.appendChild(ta)
      ta.select()
      document.execCommand("copy")
      document.body.removeChild(ta)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  /** 新 tab 打开 markdown 文档预览 */
  const handleView = async () => {
    setError(null)
    let markdown = preprocessReportMarkdown(content, sources)

    // 有 threadId 时优先拉取服务端版本（包含完整 sources）
    if (threadId) {
      try {
        const res = await fetch(`/api/report/${threadId}`)
        if (res.ok) {
          const data = await res.json()
          markdown = preprocessReportMarkdown(data.report || content, data.sources || sources)
        }
      } catch {
        // 拉取失败就用本地 content
      }
    }

    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    window.open(url, "_blank")
    // 延迟释放，确保新 tab 已加载
    setTimeout(() => URL.revokeObjectURL(url), 60000)
  }

  /** 下载 markdown 文件 — 优先走后端（含真实 sources），失败则本地拼装 */
  const handleDownload = async () => {
    setDownloading(true)
    setError(null)
    try {
      let blob: Blob
      let filename = "deepresearch_report.md"

      if (threadId) {
        try {
          const res = await fetch(`/api/report/download/${threadId}`)
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          blob = await res.blob()
          // 从 Content-Disposition 拿文件名
          const cd = res.headers.get("Content-Disposition") || ""
          const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
          if (m) filename = decodeURIComponent(m[1])
          const xname = res.headers.get("X-Filename")
          if (xname) filename = xname
        } catch (err) {
          console.warn("后端下载失败，改用本地内容", err)
          const markdown = preprocessReportMarkdown(content, sources)
          blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" })
        }
      } else {
        const markdown = preprocessReportMarkdown(content, sources)
        blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" })
      }

      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      // 立刻 revoke 可能在某些浏览器下导致下载失败，延迟
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) {
      console.error("Download failed", err)
      setError("下载失败，请重试")
      setTimeout(() => setError(null), 3000)
    } finally {
      setDownloading(false)
    }
  }

  /** 导出 PDF / Word —— 后端返回文件流，用 a[download] 触发下载 */
  const handleExport = (format: "pdf" | "docx") => {
    if (!threadId) return
    setError(null)
    const a = document.createElement("a")
    a.href = `/api/report/export/${threadId}?format=${format}`
    a.download = ""
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  return (
    <div className={cn("flex flex-wrap items-center gap-2 pt-2", className)}>
      <button
        type="button"
        onClick={handleCopy}
        className="flex items-center gap-1.5 rounded-xl border border-neutral-200 bg-white px-3 py-1.5 text-xs text-neutral-600 shadow-sm transition-colors hover:bg-neutral-100 hover:text-neutral-900"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        {copied ? "已复制" : "复制报告"}
      </button>

      <button
        type="button"
        onClick={handleView}
        className="flex items-center gap-1.5 rounded-xl border border-neutral-200 bg-white px-3 py-1.5 text-xs text-neutral-600 shadow-sm transition-colors hover:bg-neutral-100 hover:text-neutral-900"
      >
        <Eye className="h-3.5 w-3.5" />
        查看完整文档
      </button>

      <button
        type="button"
        onClick={handleDownload}
        disabled={downloading}
        className="flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-colors hover:bg-blue-500 disabled:opacity-60"
      >
        {downloading ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Download className="h-3.5 w-3.5" />
        )}
        {downloading ? "下载中..." : "下载 Markdown"}
      </button>

      <button
        type="button"
        onClick={() => handleExport("docx")}
        className="flex items-center gap-1.5 rounded-xl border border-neutral-200 bg-white px-3 py-1.5 text-xs text-neutral-600 shadow-sm transition-colors hover:bg-neutral-100 hover:text-neutral-900"
      >
        <FileText className="h-3.5 w-3.5" />
        导出 Word
      </button>

      <button
        type="button"
        onClick={() => handleExport("pdf")}
        className="flex items-center gap-1.5 rounded-xl border border-neutral-200 bg-white px-3 py-1.5 text-xs text-neutral-600 shadow-sm transition-colors hover:bg-neutral-100 hover:text-neutral-900"
      >
        <FileText className="h-3.5 w-3.5" />
        导出 PDF
      </button>

      {error && (
        <span className="text-xs text-red-500">{error}</span>
      )}
    </div>
  )
}
