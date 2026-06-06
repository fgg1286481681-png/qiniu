# backend

Python 标准库 HTTP 服务，使用 SQLite 保存项目状态，并在本地项目目录保存 Agent 中间产物和最终剧本。

启动：

```powershell
cd E:\agent\七牛云\backend
py -m pip install -r requirements.txt
py server.py
```

接口：

- `GET /api/health`
- `GET /api/projects`
- `GET /api/projects/{id}`
- `POST /api/analyze`
- `POST /api/generate`
- `POST /api/generate-async`
- `POST /api/demo/import`
- `POST /api/ai/health`
- `POST /api/projects/{id}/cancel`
- `POST /api/validate`
- `DELETE /api/projects/{id}`

异步任务在单进程 `ThreadPoolExecutor` 中执行，默认最多并发 2 个。服务重启后，未完成任务会标记为 `interrupted`。

取消采用逻辑取消。任务收到取消请求后标记为 `cancelling`，当前模型 HTTP 请求返回时停止进入下一阶段，最终状态为 `cancelled`。

AI 自检通过四个最小 Chat Completions 请求验证模型名称和服务连通性，不返回或记录 API Key。

长文本处理：

- Reader 按字符预算切分段落，逐块抽取章节、摘要、事件、人物和地点，再归并为统一章节序列。
- Planner 按章节字符预算分批抽取事实层，最终统一去重人物、地点和事件。
- Writer 按总输入预算动态分配每章摘录长度，并同时使用 Reader 摘要。

项目历史：

- `GET /api/projects` 返回项目元数据列表。
- `GET /api/projects/{id}` 恢复原文、剧本、YAML、质量指标、Trace 和修复历史。
- `DELETE /api/projects/{id}` 同步删除 SQLite 记录和本地项目目录。

测试与评测：

```powershell
cd E:\agent\七牛云
py -m unittest discover -s tests -v
py scripts\evaluate.py
```
