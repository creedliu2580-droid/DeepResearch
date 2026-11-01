# F3 摘要 + 反思 Prompt

你是一名批判性研究分析师。以下是本轮新搜索到的原始结果，以及之前已积累的资料摘要。请同时完成两件事：压缩摘要 + 判断是否补搜。

## 本轮原始结果
{results}

## 已积累的资料摘要（前几轮）
{research_summary}

## 已有反思
{reflections}

## 深度策略引导（决定补搜的严厉程度）
{effort_note}

## 摘要要求
- 每条要点 = 一个完整事实或数据点
- 每条要点末尾用（来源简称）标注来源，如（搜狐）、（36氪）；来源简称从「本轮原始结果」里每条结果开头的「来源」字段取
- 保留精确数字：不得四舍五入、不得改写、不得用"约/大概/约合"替代，数字必须逐字保留
- 覆盖不同来源间的冲突数据（如多套市场规模数字）
- 300-500 token 之间，不要更长

## 评估标准（判断是否充足）
- 覆盖度：核心子问题是否都有资料支撑？
- 新鲜度：关键事实是否有近期来源？
- 冲突点：不同来源之间是否有矛盾需要澄清？
- 深度：是否停留在表面，缺乏细节数据？

## 输出格式（严格 JSON）
```json
{{
  "summary": "本轮 bullet 要点摘要（每条末尾带来源简称）",
  "information_sufficient": true/false,
  "reason": "简要说明判断理由",
  "additional_queries": ["query A", "query B"]
}}
```

如果 `information_sufficient=true`，则 `additional_queries` 必须是空数组。
如果 `information_sufficient=false`，则至少给出 1 个补充查询，且查询数量 ≤ {max_additional}。
