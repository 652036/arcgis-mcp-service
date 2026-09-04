# ArcGIS Pro MCP

让 MCP 客户端在明确的权限和路径边界内调用 ArcPy，并在需要时接入正在运行的 ArcGIS Pro。项目同时支持磁盘工程处理、Python `CURRENT` 窗口宿主，以及可选的 ArcGIS Pro SDK 原生控制面。

2.0 版提供 400+ 个已注册工具，覆盖工程、地图、图层、布局、数据、制图、栅格、LAS、空间统计、网络、企业地理数据库、发布和实时窗口控制。这个数字会随版本变化；请始终以 `arcgis_pro_server_capabilities()` 和 `arcgis_pro_tool_info()` 的运行时结果为准。

> 项目状态：Beta。真实 GIS 执行需要 Windows、ArcGIS Pro，以及能够 `import arcpy` 的 ArcGIS Pro Python 环境。仓库采用 MIT License。

[快速开始](#快速开始) · [三种执行模式](#三种执行模式) · [Python 当前窗口](#接入-python-current-窗口) · [SDK 原生控制](#sdk-add-in-原生控制) · [安全配置](#安全模型) · [能力范围](#能力范围) · [故障排查](#故障排查)

## 三种执行模式

三种模式解决的是不同问题，不会相互静默降级：

| 模式 | 调用标识 | 执行位置 | 适用场景 | 影响已打开窗口 |
| --- | --- | --- | --- | --- |
| 文件模式 | 允许范围内的绝对 `.aprx` 路径 | 独立 ArcGIS Pro Python 进程 | 批处理、工程检查、数据生产、导出 | 否 |
| Python `CURRENT` 宿主 | 精确值 `aprx_path="CURRENT"` | ArcGIS Pro 内运行的 Python 宿主 | 当前工程、活动视图、选择、布局、刷新和大部分既有工具 | 是 |
| SDK Add-In | 不透明 `sdk_session_ref` | ArcGIS Pro SDK Add-In | 原生事件、DrawComplete、相机/时间、可取消 GP、Undo/Redo、`EditOperation` | 是 |

```text
MCP 客户端
    │ stdio
    ▼
ArcGIS Pro MCP 服务
    ├─ 绝对 .aprx ───────────────► 独立 ArcPy：文件模式
    ├─ aprx_path=CURRENT ────────► Python 宿主 v4：当前窗口
    └─ arcgis_pro_sdk_* ─────────► SDK Add-In：租约 / 事件 / 原生编辑
```

绝对 `.aprx` 永远按文件模式处理。只有显式传入 `CURRENT` 才路由到 Python 宿主；宿主失联、重启或工程切换时会失败关闭，不会转而修改磁盘工程。实时接入有两条明确路径：Python 工具箱/脚本适合复用现有 `arcpy.mp` 工具，SDK Add-In 适合原生事件、响应式界面和 `EditOperation`。两者使用独立的发现、鉴权与控制协议，也不会自动取得当前工程的控制权。

更完整的协议说明见 [实时窗口控制架构](docs/WINDOW_CONTROL.md)。

## 能力范围

运行时工具目录会给每个工具标注只读/写入、所需路径根、窗口要求和额外门禁。当前主要覆盖：

- 工程与目录：发现/摘要、导入文档、保存副本、缓存释放、连接修复、MAPX/LYRX。
- 地图与制图：地图、图层、独立表、布局、地图框、书签、报表、图表、标注、符号系统、CIM 受控写入和导出。
- 表与要素：字段/域/索引、受约束的 `arcpy.da` 查询和写入、选择集、关系、编辑器追踪、GlobalID、属性规则、字段组和条件值。
- 矢量与空间分析：裁剪、叠加、缓冲、连接、转换、空间统计、回归、聚类、时空立方体和预测。
- 栅格与地形：栅格属性、统计量、金字塔、NoData、地图代数、水文/距离分析、镶嵌数据集、LAS 数据集与金字塔。
- 网络与定位：本地网络数据集的路线、服务区、最近设施和 OD 成本矩阵；本地 locator 的批量与反向地理编码。
- 企业能力：企业地理数据库连接、版本化、协调/提交、数据维护、Utility Network 查询/验证/追踪/子网更新与导出。
- 发布：共享草稿、服务定义暂存与发布，并对 Portal/Server、公开共享及覆盖发布分别设门禁。
- 实时控制：Python `CURRENT` 视图和选择操作；SDK 活动上下文、事件、相机、时间、原生编辑和可取消的白名单 GP 作业。

本项目不是任意 Python、任意 CIM、任意 GP 或桌面鼠标点击代理。通用 GP 默认关闭；SDK 也只接受代码中已有的 typed contract。

## 环境要求

- Windows（遵循目标 ArcGIS Pro 版本的系统要求）
- ArcGIS Pro 及其自带或克隆的 Python 环境
- Python 3.10+
- `mcp>=1.20,<2`
- 构建可选 SDK Add-In 时：ArcGIS Pro 3.6、.NET 8/Visual Studio 2022 和 ArcGIS Pro SDK for .NET 3.6

常见解释器位置如下；安装目录和环境名称可能不同：

```text
C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
```

## 快速开始

### 1. 安装

推荐在 ArcGIS Pro 克隆的可写 Python 环境中安装：

```powershell
git clone https://github.com/652036/arcgis-mcp-service.git
Set-Location arcgis-mcp-service
python -m pip install -e .
python -c "import arcpy; print(arcpy.GetInstallInfo()['Version'])"
```

服务使用 stdio。手动执行 `python -m arcgis_pro_mcp` 适合排错；正常情况下由 MCP 客户端启动。

### 2. 配置 MCP 客户端

下面是通用 `mcpServers` 示例。请替换为自己的解释器、仓库和最小必要数据目录，不要把本机凭据或真实内部路径提交到公开仓库。

```json
{
  "mcpServers": {
    "arcgis-pro": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": ["-m", "arcgis_pro_mcp"],
      "cwd": "C:\\path\\to\\arcgis-mcp-service",
      "env": {
        "ARCGIS_PRO_MCP_ALLOW_WRITE": "0",
        "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "0",
        "ARCGIS_PRO_MCP_INPUT_ROOTS": "C:\\GIS_Data",
        "ARCGIS_PRO_MCP_PROJECT_ROOTS": "C:\\GIS_Projects",
        "ARCGIS_PRO_MCP_EXPORT_ROOT": "C:\\GIS_Outputs",
        "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": "C:\\GIS_Outputs\\GP",
        "ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST": "SQL_SERVER|db.example.internal",
        "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "0"
      }
    }
  }
}
```

多个输入或工程根目录使用 Windows 的路径分隔符 `;`。配置变更后应重启 MCP 客户端；Python 窗口宿主也需要重新接入，才能收到新的策略快照。

### 3. 探测实际能力

每次会话先调用：

```text
arcgis_pro_environment_info()
arcgis_pro_server_capabilities()
```

查看某个工具的完整 schema、风险和前置条件：

```text
arcgis_pro_tool_info(name="arcgis_pro_network_solve_route")
```

不要依赖 README 中的静态工具清单。`arcgis_pro_server_capabilities()` 会返回当前注册工具、只读/写入分类、路径要求、附加门禁和窗口状态。

### 4. 文件模式示例

```text
arcgis_pro_project_summary(aprx_path="C:\\GIS_Projects\\demo.aprx")
arcgis_pro_list_maps(aprx_path="C:\\GIS_Projects\\demo.aprx")
arcgis_pro_list_layers(aprx_path="C:\\GIS_Projects\\demo.aprx", map_name="Map")
```

文件模式适合自动化和可重复生产，但它没有“当前活动窗格”语义，也不会让用户眼前的地图立即变化。

## 接入 Python `CURRENT` 窗口

Python 宿主能复用大量现有 `aprx_path` 工具，是接入当前工程最简单的方式。

1. 在 ArcGIS Pro 中打开并保存目标工程。
2. 在 Catalog 中添加仓库根目录的 `接入当前窗口.pyt`，运行“接入当前窗口”并保持运行。
3. 调用 `arcgis_pro_window_status()`。
4. 要求 `window_attached=true`、`host_ready=true`、`target_confirmed=true`，并人工核对 `current_project`。
5. 用 `arcgis_pro_active_view_info(aprx_path="CURRENT")` 做只读 smoke test。
6. 只有操作眼前窗口时，后续工具才传 `aprx_path="CURRENT"`。

窗口启动器会优先使用仓库根目录 `.arcgis-pro-mcp-deps` 中的兼容 FastMCP，避免修改 ArcGIS Pro 的系统环境。若启动时报 `No module named 'mcp'`，在 PowerShell 中运行（按实际路径替换）：

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" -m pip install --target "C:\path\to\arcgis-mcp-service\.arcgis-pro-mcp-deps" "mcp>=1.20,<2"
```

安装后重新运行“接入当前窗口”即可，不需要用 `ArcGISPro.exe -m pip`。

也可在 ArcGIS Pro 的 Python 窗口中启动：

```python
import runpy
runpy.run_path(r"C:\path\to\arcgis-mcp-service\接入当前窗口.py")
```

协议 v4 使用随机会话令牌、仅本机 loopback、原子私有发现文件、目标工程锁存、有界串行队列、排队取消和 job/event 状态。Windows 发现状态位于当前用户的 `%LOCALAPPDATA%\ArcGISProMcp\window-host`，写入时使用受保护的当前用户 ACL；读取方拒绝链接/reparse point、异常大小、错误所有者或非私有 DACL。它是本机能力凭据，不应复制、共享或提交。工程切换、宿主重启或 session 变化后，必须重新调用 `arcgis_pro_window_status()` 确认目标。

Python 工具/窗口运行在 Pro foreground，适合 Agent 在一段时间内独占控制；长任务期间 Pro 交互可能受限。需要持续事件、原生 Undo/Redo、DrawComplete 或可取消后台作业时，使用 SDK Add-In。

## SDK Add-In 原生控制

仓库的 [`sdk/ArcGISProMcp.AddIn`](sdk/ArcGISProMcp.AddIn) 包含 ArcGIS Pro 3.6 Add-In 源码。它需要单独构建和安装，不会因安装 Python 包而自动加载：

```powershell
Set-Location sdk\ArcGISProMcp.AddIn
dotnet restore .\ArcGISProMcp.AddIn.csproj
dotnet build .\ArcGISProMcp.AddIn.csproj -c Release
```

Add-In 只监听 `127.0.0.1` 的系统分配端口，使用每次加载生成的随机 bearer/session token，并将发现文件限制为当前 Windows 用户。MCP 响应不会暴露 token 或 lease secret。

典型工作流：

```text
arcgis_pro_sdk_bridge_status()
arcgis_pro_sdk_acquire_project_lease(expected_project_uri="C:\\GIS_Projects\\demo.aprx")
arcgis_pro_sdk_context(sdk_session_ref="...")
arcgis_pro_sdk_set_camera(..., expected_context_generation=..., confirm=true)
arcgis_pro_sdk_wait_events(...)
arcgis_pro_sdk_release_project_lease(...)
```

租约绑定一个精确、已保存的 `.aprx`，默认 45 秒，可续期，且同一 Add-In 同时只有一个控制者。工程切换、租约过期、Add-In 重启或 URI 不匹配会失败关闭。

SDK 原生控制提供：

- 活动视图、相机、图层、选择摘要、时间和多组 generation 的一致快照；
- 相机、按 URI 缩放、刷新并等待 DrawComplete、时间范围和打开已加载表；
- 活动窗格、相机、选择、编辑、绘制、时间及工程事件的有界长轮询；
- `EditOperation` 创建/修改/删除、原生 Undo/Redo、保存/丢弃编辑；
- 严格 typed contract、双 allowlist 和输入/输出路径约束下的异步 GP job、状态与协作取消。

SDK 写请求使用 `expectedMapUri`、context/selection/edit generation、选择数量及 OID digest 做 compare-and-swap。收到冲突时先重新读取上下文，不要盲目重试。完整端点和请求契约见 [SDK bridge README](sdk/ArcGISProMcp.AddIn/README.md)。

## 安全模型

普通读写默认开启；设置 `ARCGIS_PRO_MCP_ALLOW_WRITE=0` 可显式切换为只读。其余高风险开关默认关闭，基础写入权限也不是万能授权；删除、发布、企业维护、CIM 与 SDK 编辑仍必须同时满足更窄的门禁、精确目标确认和路径策略。

| 配置 | 作用 |
| --- | --- |
| `ARCGIS_PRO_MCP_ALLOW_WRITE` | 普通写入总开关，默认开启；设为 `0`、`false`、`no` 或 `off` 可关闭 |
| `ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE=1` | 删除、覆盖、丢弃编辑等破坏性操作 |
| `ARCGIS_PRO_MCP_ALLOW_CIM_WRITE=1` | 原始 CIM 写入 |
| `ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE=1` | 企业版本管理、维护和 Utility Network 管理操作；不替代普通要素/行编辑授权 |
| `ARCGIS_PRO_MCP_ALLOW_PUBLISH=1` | 发布操作 |
| `ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE=1` | 向 `EVERYONE` 共享 |
| `ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE=1` | 覆盖既有发布服务 |
| `ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD=1` | 允许显式内联数据库密码；默认应使用环境变量或连接文件 |
| `ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST` | `平台|实例` 形式的精确数据库目标白名单；创建 `.sde` 时必须配置 |
| `ARCGIS_PRO_MCP_DB_USERNAME` / `ARCGIS_PRO_MCP_DB_PASSWORD` | 创建数据库连接专用的固定凭据变量；工具不能自行指定其他环境变量名 |
| `ARCGIS_PRO_MCP_INPUT_ROOTS` | 限制允许读取的数据根目录 |
| `ARCGIS_PRO_MCP_PROJECT_ROOTS` | 限制 `.aprx` 根目录；未设置时回退到输入根 |
| `ARCGIS_PRO_MCP_EXPORT_ROOT` | 约束地图、布局、报表、图表及审计导出 |
| `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` | 写入型 GP 的强制输出根目录 |
| `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP=1` + `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST` | Python 通用 GP 的双重开关 |
| `ARCGIS_PRO_MCP_PORTAL_ALLOWLIST` / `ARCGIS_PRO_MCP_SERVER_ALLOWLIST` | 发布和企业连接目标限制 |
| `ARCGIS_PRO_MCP_SDK_GP_ALLOWLIST` / `ARCGIS_PRO_MCP_SDK_GP_ENV_ALLOWLIST` | SDK GP 工具和环境白名单 |
| `ARCGIS_PRO_MCP_SDK_ALLOW_EDIT_COMMANDS=1` | SDK Undo/Redo/保存类编辑命令 |
| `ARCGIS_PRO_MCP_SDK_ALLOW_FEATURE_EDITS=1` | SDK 原生要素创建/修改/删除 |
| `ARCGIS_PRO_MCP_SDK_ALLOW_DISCARD_EDITS=1` | SDK 丢弃全部待保存编辑 |
| `ARCGIS_PRO_MCP_HOST_PORT` | 将一个 Python `CURRENT` 宿主绑定到一个显式 loopback 端口 |

此外：

- 不要把密码、Portal token、窗口 bearer、租约 ID 或连接字符串提交到仓库、Issue、日志或截图。
- 数据库连接优先使用 ArcGIS 管理的现有连接文件。创建新 `.sde` 时必须命中 `ARCGIS_PRO_MCP_DB_INSTANCE_ALLOWLIST`，只会读取固定的 `ARCGIS_PRO_MCP_DB_USERNAME` / `ARCGIS_PRO_MCP_DB_PASSWORD`；内联密码默认拒绝，凭据默认不保存到连接文件。
- 写入型 GP 必须位于已配置的 GP 输出根下。通用 GP 还必须同时开启并精确 allowlist，每次提供至少一个完整 `out_*` 目标路径；输出容器与名称分离、原地/无输出、破坏性和代码执行工具均被拒绝。
- 通用 GP 和 `CURRENT` 窗口分析拒绝已有输出，并在执行期强制 `overwriteOutput=False`。地图、布局、报表、图表、工程副本以及本地发布草稿/服务定义导出同样要求新文件；外部服务覆盖仍由独立发布覆盖门禁控制。
- `arcgis_pro_gp_calculate_field` 只接受受限的纯 Arcade 表达式，不接受 Python/VB/code block 或远程动态取数；标注表达式也仅允许 Arcade，相关 CIM 标注写入还要求 CIM 门禁。`arcgis_pro_gp_repair_geometry` 固定使用 `KEEP_NULL`，不会借修复之名删除空几何记录。
- 普通要素/行编辑由 `ARCGIS_PRO_MCP_ALLOW_WRITE` 授权；删除等操作再要求 destructive，SDK 原生要素编辑再要求 SDK feature gate。`ARCGIS_PRO_MCP_ALLOW_ENTERPRISE_WRITE` 只额外保护企业版本管理、维护和 Utility Network 管理操作。
- 删除/覆盖类工具通常还要求 `expected_count`、目标路径或专用 `confirm_*` 参数。
- 文件模式缓存的 `arcgis_pro_release_project` / `arcgis_pro_reload_project` 不会保存待处理更改；两者要求 WRITE + DESTRUCTIVE，并要求 `confirm_aprx_path` 与 `aprx_path` 完全一致。
- 运行中超时不代表失败。先重新读取工程、选择或输出状态，再决定是否重试非幂等操作。
- 不要把 Python 宿主或 SDK loopback 端口代理、端口转发或暴露到其他机器。

完整策略见 [SECURITY.md](SECURITY.md) 和 [skill 安全矩阵](skills/arcgis-pro-mcp/references/security-and-paths.md)。

## 故障排查

### `cannot import name 'FORWARDED_ENV_KEYS'`

这通常不是缺少依赖，而是 ArcGIS Pro 进程仍缓存旧版 `arcgis_pro_mcp.pro_attach`，却开始加载新版 `pro_host`。2.0 的包外 bootstrap 会整代清除并重新载入 `arcgis_pro_mcp.*`，但升级已经加载的旧工具箱时仍需先退出旧宿主：

1. 取消正在运行的 `.pyt` 或在 Python 窗口按 Ctrl+C，等待输出“窗口宿主已停止”。
2. 确保仓库代码是同一完整版本，不要只替换单个 `.py` 文件。
3. 在 Catalog 刷新工具箱；如仍保留旧入口，移除后重新添加 `接入当前窗口.pyt`。
4. 或先在 Python 窗口运行一次 `runpy.run_path(...)` 的 `.py` 入口。
5. 再启动宿主并重启 MCP 客户端。只有旧类或中断标记仍被 Pro 持有时才需要重启 ArcGIS Pro。

不要在宿主仍运行时强制 `importlib.reload()`；它可能混用两代模块和工具注册。

### `No module named 'mcp'`

不要对 `ArcGISPro.exe` 使用 `-m pip`。按“接入当前窗口”一节的命令，用 ArcGIS Pro 环境内的 `python.exe` 将 `mcp>=1.20,<2` 安装到仓库本地 `.arcgis-pro-mcp-deps`，然后重新运行工具箱入口。启动器会自动发现该目录。

### `window_attached=false` 或 `target_confirmed=false`

- 确认工程已保存、宿主仍在运行，且 stdio 与 Pro 使用匹配的宿主端口。
- 每次宿主重启或切换工程后重新调用 `arcgis_pro_window_status()`。
- 多个 Pro 实例应配置不同 `ARCGIS_PRO_MCP_HOST_PORT`；不要依赖“第一个窗口”。

### SDK bridge 未发现或发现多个

- 确认 Add-In 已安装并在打开的 ArcGIS Pro 中加载。
- 多实例时先读取脱敏状态，再给 `arcgis_pro_sdk_bridge_status(process_id=...)` 指定 PID。
- discovery/lease 失效时重新获取状态和租约，不要复用旧 `sdk_session_ref`。

### 工具看得到但调用被拒绝

调用 `arcgis_pro_tool_info(name="...")` 查看所需 gate、root、`CURRENT` 或 SDK 上下文。修改环境变量后重启 MCP 客户端；窗口宿主还要重新接入。

## 开发与验证

普通 Python 可做语法、lint 和无 ArcPy 单元测试，但不能证明真实 ArcPy 行为：

```powershell
python -m pip install -e ".[dev]"
ruff check .
python -m compileall -q arcgis_pro_mcp
python -m py_compile arcgis_pro_mcp_bootstrap.py "接入当前窗口.py" "接入当前窗口.pyt"
python -m unittest discover -s tests -p "test_*.py"
```

真实变更还应使用目标 ArcGIS Pro Python 做 smoke test；SDK 改动需在 ArcGIS Pro SDK 3.6 工具链中构建并在 Pro 内验证。贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档

- [实时窗口控制架构](docs/WINDOW_CONTROL.md)
- [SDK Add-In 协议与构建](sdk/ArcGISProMcp.AddIn/README.md)
- [安全策略](SECURITY.md)
- [英文更新日志](CHANGELOG.md) / [中文更新日志](CHANGELOG.zh-CN.md)
- [贡献指南](CONTRIBUTING.md)

## License

[MIT](LICENSE)
