# DeepResearch 深度研究助手

用户输入一个宽泛的研究需求，系统自动完成「需求澄清 → 联网深度研究 → 撰写报告」，产出结构完整、带来源引用、经过质量评审的研究报告；报告生成后还可针对报告继续追问（报告能答就用报告答，答不上则自动联网搜索）。

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | Vite + React + TypeScript + Tailwind + Zustand |
| 后端 | Python 3.11 + FastAPI + LangGraph（主图 + Research/Writing 子图，HITL interrupt，流式 SSE） |
| 搜索 | Tavily（异步并行） |
| LLM | OpenAI 兼容协议，多厂商声明式配置（`llm.yaml`），按角色分配 temperature |
| 存储 | SQLite（LangGraph 检查点 + 会话元数据） |

## 目录结构

```
backend/
  app/
    main.py            # FastAPI 入口：SSE 网关 + REST 接口 + 健康检查/指标
    settings.py        # 集中配置（effort / tavily / writing / llm / concurrency / server）
    state.py           # LangGraph 共享状态
    llm.py             # invoke_llm：超时 + 供应商 failover + 并发限流
    limits.py          # 进程内并发信号量
    observability.py   # 指标计数 + JSON 日志 + 请求 ID
    sessions.py        # 会话元数据（标题/时间），用于历史会话列表
    export.py          # 报告导出 PDF / Word
    citations.py       # 来源去重 / 短链还原 / 引用提取 / 数字核对
    config/llm.yaml    # 多厂商 LLM 声明式配置
    config/loader.py   # llm.yaml 解析 + ${ENV} 占位 + 校验
    models/factory.py  # ModelFactory：role × model 实例缓存
    prompts/           # prompt 模板（.md）
    graph/main.py      # 主图：plan → interrupt → research → write → qa
    subgraphs/
      research.py      # Research 子图：拆查询 → 并行搜索 → 摘要+反思循环
      writing.py       # Writing 子图：大纲 → 草稿 → 审稿修订循环 → 润色
    tools/search.py    # SearchTool 抽象 + Tavily 实现
  evaluate/            # 质量评估框架（LLM-as-Judge，PRD F8）
  tests/               # 假模型/假搜索全链路自测（无需真实 key）
frontend/
  src/
    App.tsx            # SSE 事件分发 + 状态编排
    api/client.ts      # fetchModels + streamPost（SSE 客户端）
    store/             # Zustand 状态（消息/时间线/会话）
    components/        # 消息列表 / 输入框 / 时间线 / 历史会话 / 报告操作等
```

## 快速开始

### 1. 后端

```bash
cd backend
python -m venv .venv            # 首次创建虚拟环境
# Windows:
.venv\Scripts\python -m pip install -r requirements.txt
# macOS/Linux:
.venv/bin/pip install -r requirements.txt

cp .env.example .env            # 填入真实 key（见下方配置）
# Windows:
.venv\Scripts\python -m app.main    # 默认端口 8010
# 或：
.venv\Scripts\python -m uvicorn app.main:app --port 8010 --reload
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173，/api 已代理到 8010
```

浏览器访问 **http://localhost:5173**（用 `localhost`，Vite 绑定 IPv6 回环地址）。

## 配置

### `app/config/llm.yaml`（多厂商 LLM）

```yaml
providers:
  deepseek:
    base_url: "https://api.deepseek.com/v1"
    api_key: "${DEEPSEEK_API_KEY}"      # ${ENV} / ${ENV:default} 占位
    default_model: "deepseek-flash"
models:
  - { name: "deepseek-flash", provider: "deepseek" }
  - { name: "deepseek-v4-pro", provider: "deepseek" }
role_defaults:
  plan: ""       # 空 → 回退 default_model
  search: ""
  write: ""
  review: ""
default_model: "deepseek-flash"
```

- 换厂商（通义/智谱/Moonshot/OpenAI/Ollama）只需加 provider + models 条目；非 OpenAI 协议需在 `models/factory.py` 的 `_make_llm` 里加实现分支。
- 前端模型下拉框 = `models` 清单，用户所选模型会应用到所有角色；角色只区分 `temperature`（review 0.2 / write 0.3 / 其它 0.7）。

### `.env`（密钥 + 可调参数）

完整清单见 `backend/.env.example`，常用项：

```bash
DEEPSEEK_API_KEY=          # llm.yaml 里 ${DEEPSEEK_API_KEY} 引用
TAVILY_API_KEY=            # 联网搜索

LLM_TIMEOUT_SECONDS=240    # 单次调用超时（draft/polish 生成长报告，别设太小）
LLM_MAX_RETRIES=2
LLM_FALLBACK_MODELS=       # 主模型失败后的兜底模型（逗号分隔，留空=不启用）

TAVILY_MAX_RESULTS=3       # 每条查询返回结果数
TAVILY_SEARCH_DEPTH=advanced

WRITING_PASS_SCORE=6.0     # 审稿及格线
WRITING_MAX_REVISIONS=2    # 最大修订轮次

MAX_CONCURRENT_LLM=4
MAX_CONCURRENT_SEARCH=4
RATE_LIMIT_PER_MINUTE=60

SERVER_PORT=8010
CORS_ORIGINS=http://localhost:5173
EVAL_MODEL=deepseek-v4-pro # 评估裁判模型（可选）
```

## 核心流程

1. **需求澄清（HITL）**：生成研究计划 → `interrupt` 暂停等用户确认或修改。
2. **深度研究**：拆分独立查询 → Tavily 异步并行搜索 → 摘要 + 反思循环补搜，直到信息充足或达到轮次上限（effort：低 1×1 / 中 3×3 / 高 5×5）。
3. **报告撰写**：大纲 → 草稿 → 审稿评分（事实准确性优先）→ 修订循环（评分≥8 直接过 / ≥6 且已修订过 / 最多修订 2 次）→ 润色。
4. **引用处理**：模型只写 `[来源名](短链)`，后端用确定性代码把短链还原为真实 URL，并只返回正文实际引用的来源（杜绝幻觉 URL）。
5. **报告问答**：判断报告能否回答 → 能则基于报告答，不能则联网搜索后带引用作答。

## API

### REST

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/models` | 角色→模型映射 + 前端候选模型 |
| GET | `/api/sessions` | 历史会话列表 |
| GET | `/api/sessions/{thread_id}` | 加载某个历史会话的报告 + 来源 |
| GET | `/api/report/{thread_id}` | 最终报告 + 来源（JSON） |
| GET | `/api/report/download/{thread_id}` | 下载 Markdown |
| GET | `/api/report/export/{thread_id}?format=pdf\|docx` | 导出 PDF / Word |
| GET | `/api/chat/state/{thread_id}` | 调试：查看会话状态 |
| GET | `/health` `/health/ready` `/metrics` | 健康检查 / 就绪检查 / 指标 |

### SSE（POST，`text/event-stream`）

| 端点 | 请求体 | 说明 |
|---|---|---|
| `/api/chat/start` | `{user_input, effort, model}` | 新建会话 → plan → interrupt |
| `/api/chat/resume` | `{thread_id, feedback, confirmed}` | 确认/修改计划，恢复 interrupt |
| `/api/chat/qa` | `{thread_id, question}` | 报告问答 |

SSE 事件类型：`session` / `node_start` / `node_end` / `token` / `interrupt` / `report_done` / `answer_done` / `done` / `error`。

## 质量评估（LLM-as-Judge）

```bash
cd backend
python -m evaluate.runner --html eval_report.html      # 跑测试集全部主题
python -m evaluate.runner --topic "你的主题" --effort medium   # 单主题
```

评估框架独立于主程序（`evaluate/`），从头跑真实 agent 流水线，对最终报告按 5 维度打分（事实准确性/覆盖度/逻辑/时效/引用质量）。

## 测试

```bash
# 后端（假模型/假搜索，无需真实 key）
cd backend
.venv\Scripts\python -m pip install -r requirements-dev.txt   # 测试依赖（macOS/Linux 用 .venv/bin/python）
.venv\Scripts\python -m pytest

# 前端类型检查
cd frontend
npx tsc -b
```

## 开源说明

- **本项目无鉴权**，仅用于本地 / 开发环境，**切勿直接暴露公网**（否则任何人可调用消耗你的 API 额度）。
- `.env` 已被 `.gitignore` 忽略，密钥请自行填入，不要提交。
- 许可证：MIT（见 `LICENSE`）。

## 非目标（本期不做）

登录鉴权、RAG/知识库、移动端、多语言。
