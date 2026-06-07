# Novel2Script

七牛云 × XEngineer 暑期实训营项目：AI 小说转 YAML 结构化剧本工具。

Novel2Script 面向希望快速获得剧本初稿的小说作者和内容创作者。用户输入不少于 3 个章节的小说文本后，系统通过多 Agent 工作链完成章节理解、事实规划、剧本生成、结构校验、质量诊断和局部修复，最终输出可编辑、可校验、可导出的 YAML 剧本。

**Demo 版本：v1.0**

##演示视频【Novel2Script使用介绍-哔哩哔哩】 https://b23.tv/1GLHSr0

## 环境要求

- Windows 11 或其他能够运行 Python、Node.js 的系统
- Python 3.10 或更高版本
- Node.js 18 或更高版本
- npm

以下命令均使用 PowerShell，并假设当前终端位于仓库根目录，即包含 `README.md`、`backend` 和 `frontend` 的目录。

## 配置 AI

在仓库根目录复制环境变量模板：

```powershell
Copy-Item .\.env.example .\.env
```

编辑 `.env`：

```text
LLM_API_BASE_URL=https://你的OpenAI兼容服务地址/v1
LLM_API_KEY=你的API密钥

READER_MODEL=模型名称
PLANNER_MODEL=模型名称
WRITER_MODEL=模型名称
VALIDATOR_MODEL=模型名称
```

四个 Agent 可以使用同一个 Key 和模型，也可以分别配置不同模型。`.env` 已被 Git 忽略，不要将 API Key 提交到仓库。

## 启动项目

### 1. 启动后端

在仓库根目录执行：

```powershell
Set-Location .\backend
py -m pip install -r .\requirements.txt
py .\server.py
```

后端默认地址：

```text
http://127.0.0.1:8000
```

### 2. 启动前端

保留后端终端运行，另开一个 PowerShell 窗口，进入同一个仓库根目录后执行：

```powershell
Set-Location .\frontend
npm install
npm run dev
```

浏览器访问：

```text
http://127.0.0.1:5173
```

### 3. 无 API Key 演示

未配置 `.env` 时，项目仍可启动。点击页面顶部的“载入完整演示”，即可查看 v1.0 预生成项目，包括：

- 原创小说原文。
- Agent Trace。
- 初始剧本和最终 YAML。
- Schema 校验结果。
- 四项规则指标和三项 AI 初稿诊断。
- 一轮自动修复记录。

规则降级也能完成基础结构生成，但不能替代真实模型的改编质量。



## 项目设计

### 总体架构

```text
Vue 3 前端
  -> Python HTTP API
  -> Orchestrator
       -> Reader Agent
       -> Planner Agent
       -> Writer Agent
       -> Validator Agent
       -> 最多两轮 Writer 局部修复
  -> SQLite 项目元数据
  -> 本地项目产物
```

技术栈：

- 前端：Vue 3 + Vite
- 后端：Python + 标准库 HTTP 服务
- 数据校验：JSON Schema + PyYAML
- 持久化：SQLite + 本地文件
- AI 接口：OpenAI-compatible Chat Completions API

### Agent 工作链

1. **Reader Agent**
   - 识别小说章节。
   - 提取章节摘要、关键事件、人物和地点。
   - 长文本按段落预算分块，并归并章节结果。

2. **Planner Agent**
   - 将 Reader 结果整理为人物、地点、事件等改编事实层。
   - 长文本按章节批次规划，并统一去重实体。

3. **Writer Agent**
   - 将事实层转换为结构化剧本。
   - 生成场景、动作、对白、旁白、转场等元素。
   - 输出 JSON 对象和 YAML 文本。

4. **Validator Agent**
   - 执行 Schema 和引用一致性校验。
   - 诊断改编忠实度、场景可演性、人物与对白一致性。
   - 将问题定位到具体场景，并生成修改建议。

5. **Orchestrator**
   - 按固定顺序编排 Agent。
   - 记录进度、Agent Trace、模型来源、耗时和降级原因。
   - 对结构错误或 AI 高严重度问题触发最多两轮局部修复。

### 质量诊断

规则指标与 AI 诊断分开显示：

- 规则指标：章节覆盖率、事件覆盖率、引用一致性、场景完整率。
- AI 初稿诊断：改编忠实度、场景可演性、人物与对白一致性。
- 问题信息：严重度、场景 ID、问题描述和修改建议。
- 修复记录：修复原因、修改范围、修复前后校验与指标变化。

AI 初稿评分用于诊断和修复前后对比，不代表行业标准或客观艺术评分。

### 降级策略

Reader、Planner、Writer、Validator 均优先调用真实模型。未配置模型或模型调用失败时：

- Reader 回退到规则章节解析。
- Planner 回退到规则人物、地点和事件抽取。
- Writer 回退到规则剧本生成或规则结构修复。
- Validator 保留 Schema 与引用校验，AI 诊断标记为不可用。

发生降级时，Agent Trace 会标记为 `degraded`，并展示具体原因，不会将规则结果伪装成真实模型结果。

## 主要功能

- 粘贴小说文本，或上传多种文本、文档、电子书和 PDF 文件。
- 自动识别章节，并阻止少于 3 章的内容进入生成流程。
- 异步生成、真实阶段进度、累计计时和刷新后任务恢复。
- Reader、Planner、Writer、Validator 多 Agent 真实调用。
- 自动生成符合 Schema 的 YAML 结构化剧本。
- 展示人物表、完整场景正文、来源章节、事件和改编总结。
- YAML 在线编辑、重新校验、复制和导出。
- 最多两轮局部自动修复。
- 展示规则指标、AI 初稿诊断和修复历史。
- 展示模型、来源、耗时、重试、Token Usage 和降级原因。
- AI 配置一键自检；多个 Agent 使用相同模型时只发送一次共享模型检测。
- SQLite 历史项目保存、恢复和删除。
- 无 API Key 时可载入完整离线演示。

## 目录结构

```text
.
├─ backend/       Python 后端、Agent、Schema 和持久化逻辑
├─ frontend/      Vue 3 前端工程
├─ demo/          v1.0 原创离线演示原文和预生成项目包
├─ docs/          BRD、MRD、PRD、YAML Schema 和质量诊断文档
├─ evaluation/    离线评测输入样例和期望检查项
├─ reports/       评测脚本生成的 JSON 报告
├─ scripts/       演示包生成和离线评测脚本
├─ tests/         后端接口与核心逻辑自动化测试
├─ data/          SQLite 数据库和运行时项目文件
├─ .env.example   AI 模型和长文本预算配置模板
└─ README.md      项目总览、启动和使用说明
```

### `backend/`

后端核心目录：

- `agents/`：Reader、Planner、Writer、Validator Agent。
- `server.py`：HTTP API、异步任务、进度轮询和任务取消。
- `orchestrator.py`：Agent 编排与自动修复闭环。
- `llm_client.py`：OpenAI-compatible 模型客户端。
- `project_store.py`：SQLite 和项目文件持久化。
- `quality_metrics.py`：确定性指标与 AI 诊断结果整理。
- `schema.json`：最终 YAML 对应的 JSON Schema。
- `requirements.txt`：Python 依赖。

### `frontend/`

Vue 3 + Vite 前端：

- `src/App.vue`：主要页面、交互和项目状态管理。
- `src/styles.css`：页面样式和响应式布局。
- `vite.config.js`：开发服务器及 `/api` 后端代理。
- `package.json`：前端依赖和运行脚本。

`node_modules/`、`dist/` 是安装和构建产物，不需要提交到仓库。

### `demo/`

v1.0 离线演示资源：

- `source.txt`：原创四章短篇小说。
- `original_project.json`：预生成 Reader、Planner、Writer、Validator、修复历史和质量诊断结果。

点击前端“载入完整演示”即可导入。该演示不依赖 API Key，适合评审现场快速展示完整链路。

### `docs/`

项目需求和技术设计文档，包括：

- BRD 商业需求文档。
- MRD 市场需求文档。
- PRD 产品需求文档。
- 剧本 YAML Schema 设计说明。
- 剧本初稿质量诊断与自动修复规划。

### `evaluation/`

离线核心能力评测用例。用于验证章节处理、Schema、引用和覆盖率等确定性能力，不用于证明 AI 的主观剧本质量。

### `reports/`

`scripts\evaluate.py` 生成的评测报告目录。报告文件名包含生成日期和时间，可用于记录不同版本的规则基线。

### `scripts/`

- `evaluate.py`：运行离线核心能力评测。
- `build_demo_fixture.py`：根据原创样例重新生成 v1.0 离线演示包。

### `tests/`

Python 自动化测试，覆盖：

- 异步生成与轮询。
- 项目持久化和恢复。
- Schema 与引用校验。
- 长文本分块。
- Agent 降级状态。
- 自动修复轮次。
- 质量指标与 AI 诊断结构。

### `data/`

运行时持久化目录：

```text
data/
├─ novel2script.db
└─ projects/
   └─ 项目UUID/
      ├─ source.txt
      ├─ reader.json
      ├─ planner.json
      ├─ initial_script.json
      ├─ script.json
      ├─ script.yaml
      ├─ validation.json
      ├─ quality_metrics.json
      ├─ agent_trace.json
      └─ repair_history.json
```

`data/` 包含用户原文和本机运行数据，已通过 `.gitignore` 排除，不应提交到公开仓库。

## 主要 API

- `GET /api/health`：后端健康检查。
- `GET /api/projects`：历史项目列表。
- `GET /api/projects/{id}`：恢复完整项目。
- `DELETE /api/projects/{id}`：删除 SQLite 记录和项目文件。
- `POST /api/analyze`：规则章节识别。
- `POST /api/generate`：同步生成兼容接口。
- `POST /api/generate-async`：创建异步生成任务。
- `POST /api/demo/import`：导入 v1.0 离线演示。
- `POST /api/ai/health`：模型配置和连通性检测。
- `POST /api/projects/{id}/cancel`：请求取消异步任务。
- `POST /api/validate`：校验用户编辑后的 YAML。

任务取消采用阶段边界取消。标准库 HTTP 请求正在等待模型响应时不会被强制终止，但响应返回后不会继续执行后续 Agent。

## 运行测试

在仓库根目录执行：

```powershell
py -m unittest discover -s .\tests -v
```

构建前端：

```powershell
Push-Location .\frontend
npm install
npm run build
Pop-Location
```

运行离线核心能力评测：

```powershell
py .\scripts\evaluate.py
```

评测结果保存到：

```text
reports\日期_时间_核心能力评测报告.json
```

该报告是无模型 API 的规则基线，用于验证章节处理、Schema、引用和覆盖率，不代表真实模型的主观剧本质量。

## 长文本预算可通过 `.env` 调整

- `READER_CHUNK_CHARS`：Reader 单个段落批次字符数，默认 `12000`。
- `PLANNER_CHUNK_CHARS`：Planner 单个章节批次字符数，默认 `14000`。
- `WRITER_INPUT_CHARS`：Writer 章节摘录总字符预算，默认 `18000`。
- `LLM_DEFAULT_TIMEOUT_SECONDS`：普通模型调用超时，默认 `180` 秒。
- `LLM_MAX_RETRIES`：普通模型调用重试次数，默认 `2`。
- `LLM_HEALTH_TIMEOUT_SECONDS`：模型自检超时，默认 `20` 秒。

预算越大，模型获得的上下文越完整，但调用耗时和 Token 成本也会增加。

## 上传文件读取能力

支持格式：

- 文本类：`.txt`、`.md`、`.markdown`、`.csv`、`.tsv`、`.json`、`.yaml`、`.yml`、`.html`、`.htm`、`.xml`、`.log`
- 文档类：`.docx`
- 电子书：`.epub`
- PDF：`.pdf`

文本编码识别：

- UTF-8
- UTF-8 BOM
- UTF-16LE
- UTF-16BE
- GB18030
- GBK
- Big5
- Shift_JIS
- Windows-1252

DOCX、EPUB 和 PDF 由前端解析为纯文本后再发送给后端。扫描版 PDF 不包含可提取文本时，需要先进行 OCR。

## 相关文档

- [BRD：AI 小说转剧本工具商业需求文档](docs/20260605_1037_AI小说转剧本工具_BRD.md)
- [MRD：AI 小说转剧本工具市场需求文档](docs/20260605_1037_AI小说转剧本工具_MRD.md)
- [PRD：AI 小说转剧本工具产品需求文档](docs/20260605_1037_AI小说转剧本工具_PRD.md)
- [剧本 YAML Schema 设计说明](docs/20260605_1627_剧本YAMLSchema设计说明.md)
- [剧本初稿质量诊断与自动修复规划](docs/20260606_剧本转换质量评分体系.md)
