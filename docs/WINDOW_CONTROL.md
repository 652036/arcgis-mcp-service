# ArcGIS Pro 实时窗口控制架构

## 目标与边界

本项目同时保留两种明确的执行语义：

- **文件模式**：传入绝对 `.aprx` 路径，在独立 ArcGIS Pro Python 进程中处理工程文件。
- **窗口模式**：显式传入 `aprx_path=CURRENT`，在已接入的 ArcGIS Pro 进程中读取或改变当前工程、活动视图和显示状态。

窗口模式不应退化为“猜测当前文件在哪里”。窗口宿主失联、会话变化或用户切换工程时，请求必须失败并要求客户端重新读取状态。

## 当前实现：Python 窗口宿主 v3

```text
MCP 客户端
  │ stdio
  ▼
Python 路由器
  ├─ 绝对 .aprx ───────────────► 文件模式 ArcPy 工具
  └─ aprx_path=CURRENT
       │ 已认证的 loopback HTTP
       ▼
     ArcGIS Pro 进程内宿主
       └─ 有界串行队列 ─────────► 单请求 CURRENT 工程引用 ─► MCP 工具
```

当前窗口通道提供：

- 当前工程、宿主 PID、会话、队列、忙碌工具、活动视图与相机状态。
- 打开/聚焦地图或布局视图，关闭指定类别的视图。
- 设置或平移活动地图范围，缩放到图层、选择集或全部图层。
- 显式刷新图层，让游标或原地更新后的显示重新绘制。
- 原有带 `aprx_path` 的工程、图层、选择和布局工具可在 `CURRENT` 下执行。没有 `aprx_path` 的 DA/GP 工具不会直接进入窗口宿主；实时数据编辑将在 SDK Add-in 阶段提供明确的窗口命令。

协议必须同时满足以下条件：

1. 只有显式 `aprx_path=CURRENT` 的调用进入窗口宿主；绝对路径始终留在文件模式。
2. 状态文件中的服务名、协议版本、包版本、随机会话和随机令牌必须匹配。
3. 请求携带探测时的预期工程；排队期间工程发生切换时拒绝执行。
4. 写入开关、输入/工程/导出/GP 根目录和通用 GP allowlist 由 MCP 客户端传入，宿主不自行放宽。
5. 请求队列有上限；排队超时可取消。已开始执行后发生超时，结果属于未知状态，客户端不得自动重放写请求。
6. 每个请求只创建一个 `ArcGISProject("CURRENT")` 引用，并将同一引用用于目标核验与工具执行。
7. 默认端口只允许一个可发现宿主；显式非默认端口使用端口专属状态文件，避免多个 Pro 窗口覆盖会话目标。
8. stdio 路由器只接受最近一次 `arcgis_pro_window_status` 明确确认的 session/project；宿主重启或目标变化后必须重新确认，不能静默接管新窗口。
9. 窗口工程必须已保存并具有绝对 `.aprx` 路径；未保存工程没有稳定目标身份，宿主保持 not-ready/拒绝执行。

## 能力分级

| 能力 | 文件模式 | Python 窗口宿主 v2 | SDK Add-in 目标 |
|---|---:|---:|---:|
| 读取/修改 `.aprx` | 是 | 是 | 是 |
| 读取活动地图/视图 | 否 | 是 | 是 |
| 实时改变视图范围与焦点 | 否 | 是 | 是 |
| 请求图层刷新 | 否 | 是 | 是 |
| 用户与 Agent 同时顺畅操作 | 不适用 | 否，前台独占式 | 是 |
| 视图、选择、绘制事件订阅 | 否 | 轮询 | 是 |
| 运行中任务进度与可靠取消 | 有限 | 有限 | 是 |
| 编辑事务与 Undo/Redo | 否 | 有限 | 是 |
| 任意 Ribbon/对话框点击 | 否 | 否 | 不建议；应提供语义化命令 |

Python Window、Notebook 和前台脚本工具运行在 ArcGIS Pro 的 foreground。现有宿主适合“Agent 独占一段时间控制当前窗口”，不能作为最终的并发 GUI 控制层。ArcPy 的 `ArcGISProject.activeView` 与 `MapView` 已足够支持活动地图范围、相机和刷新等直接操作，但完整并发控制应进入 ArcGIS Pro SDK 的线程与事件模型。

官方依据：

- [ArcGISProject：CURRENT、activeView、openView 与只读引用](https://pro.arcgis.com/en/pro-app/3.6/arcpy/mapping/arcgisproject-class.htm)
- [MapView：活动相机、图层范围、平移与缩放](https://pro.arcgis.com/en/pro-app/3.6/arcpy/mapping/mapview-class.htm)
- [RefreshLayer：编辑后刷新可见地图](https://pro.arcgis.com/en/pro-app/latest/arcpy/functions/refreshlayer.htm)
- [地理处理运行模式与 foreground 限制](https://pro.arcgis.com/en/pro-app/3.6/help/analysis/geoprocessing/basics/tool-run-modes.htm)
- [ArcGIS Pro SDK 异步编程与 QueuedTask](https://pro.arcgis.com/en/pro-app/3.6/sdk/api-reference/conceptdocs/docs/ProConcepts-Asynchronous-Programming-in-ArcGIS-Pro.html)

## 下一阶段：SDK Add-in 控制面

推荐保留 Python MCP 路由器和文件模式工具，把实时窗口控制迁移到 C# Add-in：

```text
MCP 客户端
  ▼
Python MCP 路由器
  ├─ 文件/批处理 ArcPy 工作器
  └─ 认证 IPC 会话
       ▼
ArcGIS Pro SDK Add-in
  ├─ 请求协调器：session、controller lease、幂等键、deadline
  ├─ QueuedTask / MCT：地图、图层、CIM、选择和编辑
  ├─ UI Dispatcher：活动视图、DockPane 和用户提示
  ├─ Job 管理器：状态、进度、取消和结果查询
  └─ 事件流：工程、活动视图、选择、编辑、绘制完成
```

建议按以下里程碑实施：

1. **Add-in MVP**：实例发现、认证握手、当前工程/活动视图状态、相机控制、图层刷新、单控制器租约和用户撤销入口。
2. **可靠任务协议**：结构化错误码、schema hash、幂等键、任务状态查询、进度、取消、崩溃重连和多 Pro 实例选择。
3. **事件驱动同步**：工程切换、活动地图、选择变化、编辑完成和 DrawComplete 推送；响应区分 `applied` 与 `rendered`。
4. **原生编辑**：将窗口内要参与编辑会话的写操作迁移到 SDK `EditOperation`，支持 Undo/Redo 和事务结果。

## 验收条件

- 窗口宿主不存在或上下文变化时，`CURRENT` 请求绝不回退到文件模式。
- 客户端提交后切换工程，请求在执行前被拒绝，旧工程和新工程均不被误写。
- 默认只读；写开关或任一路径策略缺失时，对应操作被拒绝。
- 同一窗口最多一个写控制器，用户能在 Pro 内查看并立即撤销控制权。
- Add-in 执行期间 Pro UI 保持响应，所有 MCT/UI 操作进入官方要求的线程。
- 写请求可通过幂等键或任务状态查询确认结果；网络超时不会诱发重复写入。
- 可观察工程、视图、选择和绘制完成事件，并能区分“修改已应用”和“画面已完成渲染”。
- 自动测试覆盖鉴权失败、协议错配、项目切换、排队超时、运行中断线、停止、宿主崩溃和多实例。
