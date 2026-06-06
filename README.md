# qiniu

七牛云 × XEngineer 暑期实训营项目：AI 小说转剧本工具。

项目定位：将 3 个章节以上的小说文本自动转换为可校验、可编辑、可导出的 YAML 结构化剧本，并提供剧本 YAML Schema 设计文档。

项目后端采用 Orchestrator + Reader/Planner/Writer/Validator Agent 工作链，支持真实模型调用、规则降级、自动修复和质量评估。

## 评审版 Demo

技术栈：

- 前端：Vue 3 + Vite
- 后端：Python 标准库 HTTP 服务
- 存储：SQLite + 本地项目文件

启动后端：

```powershell
cd E:\agent\七牛云\backend
py -m pip install -r requirements.txt
py server.py
```

配置多 Agent AI：

```powershell
cd E:\agent\七牛云
copy .env.example .env
```

然后在 `.env` 中填写：

```text
LLM_API_BASE_URL=https://你的OpenAI兼容服务地址/v1
LLM_API_KEY=你的key
READER_MODEL=你的reader模型
PLANNER_MODEL=你的planner模型
WRITER_MODEL=你的writer模型
VALIDATOR_MODEL=你的validator模型
```

当前版本的 Reader、Planner、Writer、Validator 均已接入真实 API 调用。任一 Agent 未配置或调用失败时，会回退到对应规则实现，并在 Agent Trace 中明确标记为 `degraded`。

启动前端：

```powershell
cd E:\agent\七牛云\frontend
npm install
npm run dev
```

访问地址：

```text
http://127.0.0.1:5173
```

评审版 Demo 已支持：

- 粘贴小说文本或上传 TXT、Markdown、HTML、DOCX、EPUB、PDF 等文件
- 载入内置样例或原创完整离线演示项目
- 自动识别章节
- 生成结构化 YAML 剧本
- Validator 发现结构或高严重度质量问题后最多自动修复两轮
- 展示人物表、完整场景正文、来源章节和改编总结
- 展示真实 Agent 模型、来源、耗时、重试和降级原因
- 展示章节覆盖率、事件覆盖率、对白占比、引用一致性和场景完整率
- 展示 Schema 校验结果
- 异步生成、真实进度轮询和刷新后任务恢复
- 长文本按段落和章节批次分块，Reader 摘要归并后交给 Planner/Writer
- 历史项目列表、完整恢复和本地删除
- 生成过程累计计时、阶段状态和任务逻辑取消
- AI 配置一键自检，分别检测 Reader、Planner、Writer、Validator 模型
- 复制和导出 YAML

项目持久化：

- SQLite 数据库：`data\novel2script.db`
- 项目文件目录：`data\projects\项目UUID\`
- 自动保存：原文、Reader/Planner 中间结果、初始剧本、修复历史、最终 JSON/YAML、Validation、Quality Metrics 和 Agent Trace
- 项目接口：`GET /api/projects`、`GET /api/projects/{id}`、`DELETE /api/projects/{id}`

主要生成接口：

- `POST /api/generate`：兼容同步生成
- `POST /api/generate-async`：创建异步任务
- `POST /api/demo/import`：幂等导入原创离线演示项目
- `POST /api/ai/health`：执行四个 Agent 的最小模型连通性检测
- `POST /api/projects/{id}/cancel`：请求取消异步任务
- `POST /api/validate`：校验用户编辑后的 YAML

AI 自检会分别发送四次极小的模型请求，可能产生少量 Token 消耗。任务取消采用阶段边界取消：标准库 HTTP 请求正在等待模型响应时不会被强制终止，但响应返回后不会继续执行后续 Agent。

运行测试：

```powershell
cd E:\agent\七牛云
py -m unittest discover -s tests -v
```

运行离线核心能力评测：

```powershell
cd E:\agent\七牛云
py scripts\evaluate.py
```

评测结果保存到 `reports\日期_时间_核心能力评测报告.json`。该报告是无模型 API 的规则基线，用于验证章节处理、Schema、引用和覆盖率，不代表真实模型的主观剧本质量。

长文本预算可通过 `.env` 调整：

- `READER_CHUNK_CHARS`：Reader 单个段落批次字符数，默认 12000
- `PLANNER_CHUNK_CHARS`：Planner 单个章节批次字符数，默认 14000
- `WRITER_INPUT_CHARS`：Writer 章节摘录总字符预算，默认 18000
- `LLM_HEALTH_TIMEOUT_SECONDS`：单个模型自检超时，默认 20 秒

上传文件读取能力：

- 文本类：`.txt`、`.md`、`.markdown`、`.csv`、`.tsv`、`.json`、`.yaml`、`.yml`、`.html`、`.htm`、`.xml`、`.log`
- 文档类：`.docx`
- 电子书：`.epub`
- PDF：`.pdf`
- 编码识别：UTF-8、UTF-8 BOM、UTF-16LE、UTF-16BE、GB18030、GBK、Big5、Shift_JIS、Windows-1252

## 文档

- [BRD：AI 小说转剧本工具商业需求文档](docs/20260605_1037_AI小说转剧本工具_BRD.md)
- [MRD：AI 小说转剧本工具市场需求文档](docs/20260605_1037_AI小说转剧本工具_MRD.md)
- [PRD：AI 小说转剧本工具产品需求文档](docs/20260605_1037_AI小说转剧本工具_PRD.md)
- [剧本 YAML Schema 设计说明](docs/20260605_1627_剧本YAMLSchema设计说明.md)
