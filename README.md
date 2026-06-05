# qiniu

七牛云 × XEngineer 暑期实训营项目：AI 小说转剧本工具。

项目定位：将 3 个章节以上的小说文本自动转换为可校验、可编辑、可导出的 YAML 结构化剧本，并提供剧本 YAML Schema 设计文档。

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

- 粘贴小说文本或上传 TXT
- 载入内置 3 章样例
- 自动识别章节
- 生成结构化 YAML 剧本
- 展示人物表、场景表、改编总结
- 展示 Schema 校验结果
- 复制和导出 YAML

## 文档

- [BRD：AI 小说转剧本工具商业需求文档](docs/20260605_1037_AI小说转剧本工具_BRD.md)
- [MRD：AI 小说转剧本工具市场需求文档](docs/20260605_1037_AI小说转剧本工具_MRD.md)
- [PRD：AI 小说转剧本工具产品需求文档](docs/20260605_1037_AI小说转剧本工具_PRD.md)
