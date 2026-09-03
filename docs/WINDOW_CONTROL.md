# ArcGIS Pro 实时窗口控制架构

## 语义边界

项目有三个互相独立的执行面：

1. **文件模式**：工具接收允许范围内的绝对 `.aprx` 路径，在 stdio 服务进程中使用 ArcPy。
2. **Python `CURRENT` 宿主**：工具显式接收 `aprx_path="CURRENT"`，经协议 v4 转发到 ArcGIS Pro 进程内的 Python 宿主。
3. **SDK Add-In**：`arcgis_pro_sdk_*` 工具使用短租约连接可选的 C# Add-In，提供事件、原生视图命令、可取消 GP 和 `EditOperation`。

选择规则：

- 只处理磁盘工程、批量数据或导出时使用文件模式。
- 需要复用大部分现有 `aprx_path` 工具，并能接受 Pro foreground 被占用时，使用 Python `CURRENT`。
- 需要持续事件、DrawComplete、原生 Undo/Redo、选择集 CAS、用户界面保持响应或可取消异步作业时，使用 SDK Add-In。

任何模式都不会静默切换到另一个模式。特别是：`CURRENT` 失联不能回退到磁盘 `.aprx`；SDK 租约失效不能改走 Python 宿主。

## 总体结构

```text
MCP client
  │ stdio
  ▼
Python MCP router
  ├─ absolute .aprx ───────────► ArcPy file mode
  ├─ aprx_path=CURRENT
  │    │ authenticated loopback RPC
  │    ▼
  │  Python host v4 in ArcGIS Pro foreground
  │    └─ bounded serialized queue ─► one bound CURRENT reference
  └─ arcgis_pro_sdk_*
       │ authenticated loopback HTTP + exclusive lease
       ▼
     ArcGIS Pro SDK Add-In
       ├─ QueuedTask / MCT
       ├─ UI dispatcher and Pro events
       ├─ native EditOperation
       └─ typed asynchronous GP jobs
```

## Python 窗口宿主 v4

### 启动与确认

在 ArcGIS Pro 中打开并保存工程，然后运行仓库根目录的 `接入当前窗口.pyt`；也可在 Python 窗口用 `runpy.run_path(...)` 启动 `接入当前窗口.py`。宿主必须保持运行。

MCP 侧先调用：

```text
arcgis_pro_window_status()
```

只有在以下条件同时成立时才继续：

- `window_attached=true`
- `host_ready=true`
- `target_confirmed=true`
- `current_project` 是预期的已保存工程

接着用 `arcgis_pro_active_view_info(aprx_path="CURRENT")` 做只读 smoke test。宿主重启、同端口实例替换或工程切换后，stdio 路由器会清除目标锁存；必须重新读取状态并确认目标。

### 协议约束

协议 v4 的关键不变量：

1. 只有精确 `CURRENT` 进入宿主，绝对路径始终留在文件模式。
2. 发现状态中的服务名、协议版本、包版本、端口、随机 session 和随机 bearer 必须一致。
3. 宿主仅监听本机 loopback；发现状态原子写入 Windows 当前用户的 `%LOCALAPPDATA%\ArcGISProMcp\window-host`。目录和文件使用受保护的私有 ACL，读取时校验当前用户所有者、protected DACL、普通文件类型和大小，并拒绝 symlink/reparse point。状态中的 bearer 是本机能力凭据，不应复制、提交或共享。
4. 每个请求携带预期 session/project。排队期间用户切换工程时，请求在执行前被拒绝。
5. stdio MCP 的写入开关、输入/工程/导出/GP 根和 allowlist 会逐请求传入；宿主不能扩大权限。
6. 每个调用只构造并绑定一个 `ArcGISProject("CURRENT")` 引用，同时用于目标复核和工具执行。
7. 队列有界；尚未开始的过期请求可以取消。调用已经开始后再超时，其结果是**未知**，不能自动重放写请求。
8. 停止宿主会拒绝新请求并等待已开始调用结束，防止停止/接收竞态。
9. 默认端口只允许一个可发现宿主；多 Pro 实例必须为每个实例配置不同 `ARCGIS_PRO_MCP_HOST_PORT`。

未保存的 Untitled 工程没有稳定身份，宿主会保持 not-ready。改变 stdio 环境变量后要重启 MCP 客户端并重新接入宿主，旧宿主不会动态取得更宽权限。

### 实时能力与作业

Python 宿主支持当前工程、地图、图层、独立表、布局、报表、活动视图、相机、选择和刷新，并可转发所有明确支持 `CURRENT` 的工程工具。无 `aprx_path` 的纯路径型 DA/GP 工具不会被自动送入宿主。

`arcgis_pro_current_map_run_analysis` 是受控的当前地图 GP 入口，不是任意 GP 执行器。它只接受代码内列出的非破坏性工具和当前图层/表引用；必须配置 GP 输出根，并为每次调用提供完整的 `out_*` 目标路径。输出容器与名称分离、原地/无输出、已有目标、字段计算、几何修复以及其他破坏性/代码执行工具都会被拒绝；执行期固定 `overwriteOutput=False`。字段计算请使用只接受受限纯 Arcade 表达式的专用语义工具。

对于可能超过一次同步调用时限的窗口操作，可使用：

- `arcgis_pro_window_job_submit`
- `arcgis_pro_window_job_status`
- `arcgis_pro_window_job_cancel`
- `arcgis_pro_window_wait_for_change`

取消只对尚未开始或支持取消点的工作可靠；先读取最终状态，再判断是否需要重新执行。

### Python reload 边界

入口通过包外 `arcgis_pro_mcp_bootstrap.py` 清除完整 `arcgis_pro_mcp.*` 模块代际，然后重新加载宿主、共享辅助模块和工具注册。不要在宿主仍运行时仅 `importlib.reload(pro_host)`，否则可能让新版 `pro_host` 与旧版 `pro_attach` 共存，出现 `FORWARDED_ENV_KEYS` 等导入错误。

升级已加载的旧工具箱时，应先停止宿主，刷新或重新添加 `.pyt`，必要时先运行一次 `.py` 入口。ArcGIS Pro 重启是旧入口类仍驻留时的最后手段。

## SDK Add-In 原生控制面

### 发现与鉴权

Add-In 每次加载生成独立的 256-bit bearer 和 server-session token，只监听 `127.0.0.1` 的系统分配端口。发现文档写入当前用户专属的本地应用数据目录，并以 Windows ACL 限制为当前用户。MCP 层只返回脱敏状态和不透明的 `sdk_session_ref`，不会把 bearer 或 lease secret 交给模型。

所有 HTTP 请求要求 bearer。除状态与租约获取外，业务请求还要携带 server session 和 lease ID。Add-In 拒绝非 loopback 对端、异常 Host、浏览器 Origin、超限 header/body、chunked body、重复 header 和请求流水线，并对响应设置 `Cache-Control: no-store`。

### 工程租约

通过 MCP 工具取得租约：

```text
arcgis_pro_sdk_bridge_status(process_id=0)
arcgis_pro_sdk_acquire_project_lease(
    expected_project_uri="C:\\GIS_Projects\\demo.aprx",
    process_id=0,
    ttl_seconds=45
)
```

租约只绑定一个精确、已保存的绝对 `.aprx` URI，TTL 为 10–120 秒，默认 45 秒，同一 Add-In 同时只有一个独占租约。续期和释放分别使用：

```text
arcgis_pro_sdk_renew_project_lease(sdk_session_ref="...")
arcgis_pro_sdk_release_project_lease(sdk_session_ref="...")
```

工程切换、Add-In 重启、租约过期或 URI 不一致会使后续请求失败关闭，并请求取消已登记的 GP 工作。释放采用精确 lease-ID compare-and-swap，旧控制者不能释放替代租约。

### HTTP 端点与 MCP 映射

Add-In 的稳定 HTTP 控制面如下。通常应通过 `arcgis_pro_sdk_*` MCP 工具调用，而不是直接处理 discovery token。

| 方法 | 路由 | MCP 用途 |
| --- | --- | --- |
| `GET` | `/v1/status` | `arcgis_pro_sdk_bridge_status` 的底层脱敏状态 |
| `POST` | `/v1/lease/acquire` | 获取独占工程租约 |
| `POST` | `/v1/lease/renew` | 续期租约 |
| `POST` | `/v1/lease/release` | 释放租约 |
| `GET` | `/v1/context` | 活动视图、相机、图层、选择、时间和 generations |
| `GET` | `/v1/events` | 有界长轮询原生事件 |
| `POST` | `/v1/view/camera` | 设置 typed camera |
| `POST` | `/v1/view/zoom-layer` | 按精确 layer URI 缩放 |
| `POST` | `/v1/view/refresh` | 重绘并等待后续 DrawComplete |
| `POST` | `/v1/view/time` | 设置或关闭活动时间范围 |
| `POST` | `/v1/view/open-table` | 打开预期地图中已加载的 standalone table |
| `POST` | `/v1/features/create` | 创建一个原生可撤销要素 |
| `POST` | `/v1/features/modify` | 修改经选择 CAS 锁定的要素 |
| `POST` | `/v1/features/delete` | 删除经选择 CAS 锁定的要素 |
| `GET` | `/v1/edit/status` | 读取 pending edits 和 Undo/Redo 状态 |
| `POST` | `/v1/edit/undo`、`/redo` | 原生 Undo/Redo |
| `POST` | `/v1/edit/save`、`/discard` | 保存或丢弃工程数据编辑 |
| `POST` | `/v1/jobs` | 启动 typed、allowlisted 异步 GP |
| `GET` | `/v1/jobs/{jobId}` | 读取作业状态、进度与结果 |
| `POST` | `/v1/jobs/{jobId}/cancel` | 请求 SDK 协作取消 |

精确请求字段、typed GP contract 和错误语义以 [SDK bridge README](../sdk/ArcGISProMcp.AddIn/README.md) 为准。

### Context generations 与 CAS

`arcgis_pro_sdk_context` 返回稳定上下文，包括：

- 保存工程 URI、名称和只读状态；
- `activeViewType` / `activeViewUri` / `activeMapUri`；
- 相机与空间参考 WKID；
- TOC 活动图层；
- 选择总数、按图层计数和 SHA-256 OID digest（不返回 OID 或要素值）；
- 活动时间；
- `contextGeneration`、`selectionGeneration`、`editGeneration`、`drawGeneration`。

视图命令必须回传最新的 `expectedMapUri` 与 `expectedContextGeneration`。相机还必须回传 `expectedSpatialReferenceWkid`，服务不会猜测或重投影坐标。修改/删除选择集还要求 `expectedSelectionGeneration`、`expectedCount` 和目标图层的 `expectedOidDigest`；编辑命令要求 `expectedEditGeneration`。

任一值变化都会返回冲突。正确处理方式是重新读取 context/edit status，让用户或调用方重新确认目标；盲目重试会把一次已失效意图施加到新状态。

刷新命令会区分“请求已应用”和“后续 DrawComplete 已观察到”。等待超时会返回 `drawCompleted=false`，不能伪造成功渲染。

### 原生编辑边界

SDK 要素写入同时要求：

- `ARCGIS_PRO_MCP_ALLOW_WRITE=1`
- `ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS=1`
- 有效工程租约、最新 generations 和 `confirm=true`

删除还需要 `ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=1` 与 `confirm_delete_selection=true`。丢弃全部待保存编辑还需要 `ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS=1` 和专用确认。

几何契约故意保持狭窄：只支持 2D point、单部件直线 polyline 和单环闭合 polygon；WKID 和几何类型必须与要素类一致。不接受曲线、multipart、Z/M、multipoint、multipatch 或隐式投影。属性只接受数量和长度受限的简单 JSON 值及可编辑非系统字段。每次成功请求形成一个命名的原生 Undo 项。

### 异步 GP 边界

SDK GP 需要基础写入开关、`ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST`、代码内 typed contract、非空输入根、强制 GP 输出根、环境名 allowlist、有效租约和显式确认。把名称加入 allowlist 并不会使任意工具安全；没有 typed contract 的工具仍会被拒绝。

Add-In 同时只运行一个 GP job，保留有界历史，并通过 SDK cancellation token 协作取消。`cancelRequested` 只表示取消信号已发送；调用方必须检查最终状态、`cancellationRequested` 和 `leaseInvalidated`。租约失效后成功结束的作业不能当作普通成功自动消费。

## 能力对比

| 能力 | 文件模式 | Python `CURRENT` v4 | SDK Add-In |
| --- | ---: | ---: | ---: |
| 读取/修改 `.aprx` 内容 | 是 | 是 | 受控原生子集 |
| 当前活动视图/窗格 | 否 | 是 | 是 |
| 相机、缩放和时间 | 无窗口语义 | 相机/范围/缩放 | 原生 camera/time CAS |
| 图层刷新 | 否 | 请求刷新 | 刷新并观察 DrawComplete |
| 用户与 Agent 并发交互 | 不适用 | 有限，foreground 独占 | 更适合，SDK 线程模型 |
| 视图、选择、编辑、绘制事件 | 否 | 状态轮询/变化等待 | 原生有界事件流 |
| GP 进度和取消 | 同步工具为主 | 有界宿主 job | 原生异步 job/cancellation |
| 原生 Undo/Redo | 否 | 否 | 是 |
| `EditOperation` 要素编辑 | 否 | 否 | 是，typed/CAS |
| 任意 Ribbon/对话框点击 | 否 | 否 | 否 |

## 推荐实时工作流

### Python `CURRENT`

1. `arcgis_pro_environment_info()` 和 `arcgis_pro_server_capabilities()`。
2. `arcgis_pro_window_status()` 并人工确认 project/session。
3. `arcgis_pro_active_view_info(aprx_path="CURRENT")`。
4. 枚举地图/图层并使用精确 URI 或唯一 long name；不要猜重名短名称。
5. 执行最小语义工具；重要写入前再次读取窗口状态。
6. 重新读取变更对象和选择计数；只有用户要求时才保存工程。

### SDK

1. `arcgis_pro_sdk_bridge_status()`；多实例时明确 `process_id`。
2. 对预期绝对 `.aprx` 获取短租约。
3. `arcgis_pro_sdk_context()` 和必要的 `arcgis_pro_sdk_edit_status()`。
4. 将 URI、generation、count 和 digest 原样用于一次已确认命令。
5. 用 context、edit status、event 或 job status 验证结果；冲突时停止并刷新状态。
6. 长流程续期租约，结束后显式释放。

## 限制与验收原则

- 这两个实时桥都不是任意远程代码执行、任意 CIM 或桌面 GUI 自动化接口。
- Python foreground 无法保证用户与 Agent 同时顺畅操作；SDK 仍不能替代任意 Ribbon/对话框交互。
- SDK 的交互式地图绘制提示当前未提供；`promptGeometry=false`。
- 真实能力受 ArcGIS Pro 版本、扩展许可、数据源和当前编辑状态影响。
- 非 ArcGIS Python 的单元测试只能验证协议和安全逻辑，不能证明 ArcPy/SDK 运行时行为。

验收实时写入时至少应验证：目标工程不变、写入门禁生效、选择/上下文 CAS 生效、超时不触发自动重放、变更状态可重新读取；SDK 刷新还应验证 DrawComplete，原生编辑应验证 Undo/Redo。

## 官方参考

- [ArcGISProject：CURRENT、activeView、openView 与只读引用](https://pro.arcgis.com/en/pro-app/3.6/arcpy/mapping/arcgisproject-class.htm)
- [MapView：活动相机、图层范围、平移与缩放](https://pro.arcgis.com/en/pro-app/3.6/arcpy/mapping/mapview-class.htm)
- [RefreshLayer：编辑后刷新可见地图](https://pro.arcgis.com/en/pro-app/latest/arcpy/functions/refreshlayer.htm)
- [ArcGIS Pro SDK 异步编程与 QueuedTask](https://pro.arcgis.com/en/pro-app/3.6/sdk/api-reference/conceptdocs/docs/ProConcepts-Asynchronous-Programming-in-ArcGIS-Pro.html)
