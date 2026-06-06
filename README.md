# qiniu

七牛云 × XEngineer 暑期实训营项目：AI 小说转剧本工具。

项目定位：将 3 个章节以上的小说文本自动转换为可校验、可编辑、可导出的 YAML 结构化剧本，并提供剧本 YAML Schema 设计文档。

项目后端采用 Orchestrator + Reader/Planner/Writer/Validator Agent 工作链组织规则版生成流程。

## 第一版 Demo

技术栈：

- 前端：Vue 3 + Vite
- 后端：Python 标准库 HTTP 服务
- 存储：本地内存

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

当前版本的 Reader、Planner、Writer、Validator 均已接入真实 API 调用。任一 Agent 未配置或调用失败时，会自动回退到对应规则实现。

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

第一版 Demo 已支持：

- 粘贴小说文本或上传 TXT、Markdown、HTML、DOCX、EPUB、PDF 等文件
- 载入内置 3 章样例
- 自动识别章节
- 生成结构化 YAML 剧本
- 展示人物表、场景表、改编总结
- 展示 Reader / Planner / Writer / Validator Agent 工作链
- 展示 Schema 校验结果
- 复制和导出 YAML

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
