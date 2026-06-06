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
- `POST /api/validate`
- `DELETE /api/projects/{id}`

异步任务在单进程 `ThreadPoolExecutor` 中执行，默认最多并发 2 个。服务重启后，未完成任务会标记为 `interrupted`。
