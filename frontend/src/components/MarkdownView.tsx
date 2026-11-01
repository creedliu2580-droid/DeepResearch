import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import rehypeHighlight from "rehype-highlight"
import "highlight.js/styles/github.css"
import { cn } from "@/lib/utils"

interface Source {
  title: string
  url: string
  query?: string
}

interface Props {
  content: string
  className?: string
  /** 提供后会：1) 把裸 URL 替换为 [域名](url)；2) 把旧格式 [来源：标题(url)] 替换为 [域名](url)；3) 在末尾追加信息来源列表 */
  sources?: Source[]
}

/** 常见中文站点：域名 → 中文名（避免显示拼音，如 sohu.com → 搜狐） */
const CHINESE_SITE_NAMES: Record<string, string> = {
  "sohu.com": "搜狐",
  "baidu.com": "百度",
  "zhihu.com": "知乎",
  "weibo.com": "微博",
  "sina.com": "新浪",
  "sina.com.cn": "新浪",
  "qq.com": "腾讯",
  "163.com": "网易",
  "126.com": "网易",
  "bilibili.com": "哔哩哔哩",
  "36kr.com": "36氪",
  "ifeng.com": "凤凰网",
  "huxiu.com": "虎嗅",
  "jiemian.com": "界面新闻",
  "thepaper.cn": "澎湃新闻",
  "huanqiu.com": "环球网",
  "people.com.cn": "人民网",
  "xinhuanet.com": "新华网",
  "caixin.com": "财新",
  "yicai.com": "第一财经",
  "eastmoney.com": "东方财富",
  "investing.com": "英为财情",
  "marketresearchfuture.com": "MRFR",
  "csdn.net": "CSDN",
  "cnblogs.com": "博客园",
  "oschina.net": "开源中国",
  "juejin.cn": "掘金",
  "v2ex.com": "V2EX",
  "douban.com": "豆瓣",
  "toutiao.com": "今日头条",
  "bytedance.com": "字节跳动",
  "jd.com": "京东",
  "taobao.com": "淘宝",
  "tmall.com": "天猫",
  "xiaomi.com": "小米",
  "huawei.com": "华为",
  "ithome.com": "IT之家",
  "leiphone.com": "雷峰网",
  "geekpark.net": "极客公园",
  "pingwest.com": "品玩",
  "jiqizhixin.com": "机器之心",
}

/** 从 URL 提取一个简洁来源名：中文站点显示中文名，其余显示主域名（首字母大写）。 */
function shortNameOf(url: string): string {
  let host: string
  try {
    host = new URL(url).hostname.replace(/^www\./, "").toLowerCase()
  } catch {
    return url
  }
  if (!host) return url
  let best = ""
  let bestName = ""
  for (const [domain, name] of Object.entries(CHINESE_SITE_NAMES)) {
    if ((host === domain || host.endsWith("." + domain)) && domain.length > best.length) {
      best = domain
      bestName = name
    }
  }
  if (bestName) return bestName
  const labels = host.split(".").filter(Boolean)
  const name = labels.length >= 2 ? labels[labels.length - 2] : (labels[0] ?? "来源")
  return name.charAt(0).toUpperCase() + name.slice(1)
}

/** 删除报告中「来源列表 / 信息来源」等来源章节，只保留正文内联标注。
 *  统一的信息来源章节由 preprocessReportMarkdown 在末尾重新追加。 */
function stripSourceSections(content: string): string {
  const out: string[] = []
  let skipping = false
  let skipLevel = 0
  for (const line of content.split("\n")) {
    const m = line.match(/^\s*(#{1,6})\s+(.*?)\s*$/)
    if (m) {
      const level = m[1].length
      const heading = m[2].trim()
      const isSource = heading.includes("来源列表") || heading.includes("信息来源") || heading === "来源"
      if (isSource) {
        skipping = true
        skipLevel = level
        continue
      }
      if (skipping && level <= skipLevel) skipping = false
    }
    if (!skipping) out.push(line)
  }
  return out.join("\n").trim()
}

/**
 * 预处理 markdown：
 * 1. 旧格式 [来源：标题(url)] / [来源:标题(url)] → [域名](url)，正文只显示网站简称
 * 2. 裸 URL（不在 markdown 链接里、不在代码块/行内代码里）→ [域名](url)
 * 3. 去掉报告里误输出的来源章节，若给了 sources 且末尾没有「## 信息来源」章节 → 自动追加
 */
export function preprocessReportMarkdown(content: string, sources?: Source[]): string {
  let out = stripSourceSections(content)

  // 1. 旧格式来源标注 → [域名](url)，正文里只显示网站简称。
  //    兼容「来源：标题(url)」与「来源：标题](url)」两种历史写法，冒号支持中英文。
  out = out.replace(
    /\[来源[:：]\s*[^\]()]*?\s*\((https?:\/\/[^)\s]+)\)\]/g,
    (_, url) => `[${shortNameOf(url)}](${url})`,
  )
  out = out.replace(
    /\[来源[:：]\s*[^\]]*?\]\((https?:\/\/[^)\s]+)\)/g,
    (_, url) => `[${shortNameOf(url)}](${url})`,
  )

  // 2. 裸 URL → [域名](url)。负向断言避免匹配已经在 [text](url) 或 <url> 里的。
  out = out.replace(
    /(?<!\]\(|["'=<\s])(?<!\]\()https?:\/\/[^\s<>()[\]"']+/g,
    (m) => `[${shortNameOf(m)}](${m})`,
  )

  // 2.5 去掉遗留的 search.com 占位短链（死链接），避免出现点不开的假链接
  out = out.replace(/\[([^\]]*)\]\(https:\/\/search\.com\/id\/[^)]*\)/g, "$1")

  // 3. 追加信息来源
  if (sources && sources.length > 0 && !/##\s*信息来源/.test(out)) {
    const lines = sources.map((s, i) => `${i + 1}. [${s.title || shortNameOf(s.url)}](${s.url})`)
    out = out.trimEnd() + "\n\n## 信息来源\n\n" + lines.join("\n") + "\n"
  }

  return out
}

const mdComponents: Components = {
  h1: ({ className, children, ...props }) => (
    <h1 className={cn("mb-3 mt-6 text-2xl font-semibold text-neutral-900", className)} {...props}>
      {children}
    </h1>
  ),
  h2: ({ className, children, ...props }) => (
    <h2 className={cn("mb-2 mt-5 text-xl font-semibold text-neutral-900", className)} {...props}>
      {children}
    </h2>
  ),
  h3: ({ className, children, ...props }) => (
    <h3 className={cn("mb-2 mt-4 text-base font-semibold text-neutral-900", className)} {...props}>
      {children}
    </h3>
  ),
  h4: ({ className, children, ...props }) => (
    <h4 className={cn("mb-1 mt-3 text-sm font-semibold text-neutral-800", className)} {...props}>
      {children}
    </h4>
  ),
  p: ({ className, children, ...props }) => (
    <p className={cn("mb-3 leading-7 text-neutral-700", className)} {...props}>
      {children}
    </p>
  ),
  a: ({ className, children, ...props }) => (
    <a
      className={cn(
        "inline-flex items-center gap-1 rounded-md bg-blue-50 px-1.5 py-0.5 text-xs font-medium text-blue-600 no-underline transition-colors hover:bg-blue-100 hover:text-blue-700",
        className,
      )}
      target="_blank"
      rel="noopener noreferrer"
      {...props}
    >
      {children}
    </a>
  ),
  ul: ({ className, children, ...props }) => (
    <ul className={cn("mb-3 list-disc space-y-1 pl-6 text-neutral-700", className)} {...props}>
      {children}
    </ul>
  ),
  ol: ({ className, children, ...props }) => (
    <ol className={cn("mb-3 list-decimal space-y-1 pl-6 text-neutral-700", className)} {...props}>
      {children}
    </ol>
  ),
  li: ({ className, children, ...props }) => (
    <li className={cn("leading-7 marker:text-neutral-400", className)} {...props}>
      {children}
    </li>
  ),
  blockquote: ({ className, children, ...props }) => (
    <blockquote
      className={cn("my-3 border-l-2 border-neutral-300 pl-4 text-sm italic text-neutral-500", className)}
      {...props}
    >
      {children}
    </blockquote>
  ),
  hr: ({ className, ...props }) => <hr className={cn("my-5 border-neutral-200", className)} {...props} />,
  strong: ({ className, children, ...props }) => (
    <strong className={cn("font-semibold text-neutral-900", className)} {...props}>
      {children}
    </strong>
  ),
  code: ({ className, children, ...props }) => {
    const isBlock = typeof className === "string" && /language-|hljs/.test(className)
    if (isBlock) {
      return (
        <code className={cn("font-mono text-[13px]", className)} {...props}>
          {children}
        </code>
      )
    }
    return (
      <code
        className={cn(
          "rounded border border-neutral-200 bg-neutral-100 px-1.5 py-0.5 font-mono text-[13px] text-neutral-800",
          className,
        )}
        {...props}
      >
        {children}
      </code>
    )
  },
  pre: ({ className, children, ...props }) => (
    <pre
      className={cn(
        "my-3 overflow-x-auto rounded-xl border border-neutral-200 bg-neutral-100 p-4 font-mono text-[13px] leading-6",
        className,
      )}
      {...props}
    >
      {children}
    </pre>
  ),
  table: ({ className, children, ...props }) => (
    <div className="my-4 overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)} {...props}>
        {children}
      </table>
    </div>
  ),
  th: ({ className, children, ...props }) => (
    <th
      className={cn(
        "border border-neutral-200 bg-neutral-100 px-3 py-2 text-left font-semibold text-neutral-900",
        className,
      )}
      {...props}
    >
      {children}
    </th>
  ),
  td: ({ className, children, ...props }) => (
    <td
      className={cn("border border-neutral-200 px-3 py-2 align-top text-neutral-600", className)}
      {...props}
    >
      {children}
    </td>
  ),
}

export function MarkdownView({ content, className, sources }: Props) {
  const processed = sources ? preprocessReportMarkdown(content, sources) : content
  return (
    <div className={cn("text-sm text-neutral-700", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={mdComponents}>
        {processed}
      </ReactMarkdown>
    </div>
  )
}
