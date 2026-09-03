# ArcGIS Pro MCP

让 AI 客户端通过 [Model Context Protocol](https://modelcontextprotocol.io) 安全地调用 ArcPy：既能处理磁盘上的 `.aprx`，也能接入正在运行的 ArcGIS Pro 窗口，读取当前工程、控制活动视图并让修改立即反映到地图中。

当前版本注册了 217 个 MCP 工具，覆盖工程、地图、图层、布局、表与要素、符号系统、地理处理、空间统计、栅格和网络分析。实际可用工具、写入权限及路径范围始终以运行时的 `arcgis_pro_server_capabilities` 返回值为准。

> 项目状态：Beta。真实 ArcPy 执行必须使用 Windows、ArcGIS Pro 和 ArcGIS Pro 自带的 Python。

[快速开始](#快速开始) · [接入当前窗口](#接入当前-arcgis-pro-窗口) · [安全配置](#安全与路径策略) · [能力范围](#能力范围) · [故障排查](#故障排查) · [开发](#开发与验证)

## 为什么需要这个项目

普通 MCP 进程可以打开一个 `.aprx` 文件，但它不会天然控制用户眼前已经打开的 ArcGIS Pro 窗口。本项目把这两种需求拆成明确、互不混淆的执行模式：

| 模式 | `aprx_path` | 执行位置 | 适合场景 | 是否改变已打开窗口 |
| --- | --- | --- | --- | --- |
| 文件模式 | `.aprx` 的绝对路径 | 独立 ArcGIS Pro Python 进程 | 批处理、检查工程、导出、离线修改 | 否 |
| 窗口模式 | 精确值 `CURRENT` | 已接入的 ArcGIS Pro 进程 | 当前地图、活动视图、选择集、相机、实时刷新 | 是 |

窗口模式不是把某个文件路径偷偷改写成 `CURRENT`。只有调用方明确传入 `CURRENT` 时，请求才会进入窗口宿主；绝对 `.aprx` 路径永远保留文件模式语义。

```text
MCP 客户端
    │ stdio
    ▼
ArcGIS Pro MCP 路由器
    ├─ 绝对 .aprx ─────────────► 独立 ArcPy 进程（文件模式）
    │
    └─ aprx_path=CURRENT
           │ 已认证的本机 loopback 会话
           ▼
       ArcGIS Pro 窗口宿主
           └─ 有界串行队列 ────► 当前工程 / 活动视图（窗口模式）
```

## 主要特性

- 读取和修改 ArcGIS Pro 工程、地图、图层、表、布局、书签、地图框和报表。
- 接入当前 Pro 窗口，读取活动地图/布局，打开或关闭视图，控制范围、相机与缩放。
- 管理图层可见性、透明度、定义查询、选择集、标签、符号系统和数据源。
- 通过 `arcpy.da` 查询表与要素，并在显式授权后执行受约束的数据写入。
- 提供具名 GP 封装，覆盖矢量、表、转换、栅格、空间统计与网络分析。
- 使用写入开关、输入根目录、工程根目录、导出根目录和 GP 输出根目录限制操作范围。
- 窗口宿主使用随机会话令牌、目标工程校验和失败关闭机制，防止请求误投到另一个工程或 Pro 实例。
- 选择操作会核验 ArcPy 派生计数与真实 `Layer.getSelectionSet()`，不再用总行数冒充选择数。

## 环境要求

- Windows（以所用 ArcGIS Pro 版本的系统要求为准）
- ArcGIS Pro
- ArcGIS Pro 自带或由其克隆的 Python 环境
- Python 3.10+

常见的 ArcGIS Pro Python 路径：

```text
C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
```

ArcGIS Pro 的安装位置和环境名称可能不同。请让 MCP 配置指向实际能够成功执行 `import arcpy` 的解释器。

## 快速开始

### 1. 获取代码并安装

在已激活的 ArcGIS Pro Python 环境中运行；推荐使用 ArcGIS Pro 克隆的可写环境：

```powershell
git clone https://github.com/652036/arcgis-mcp-service.git
Set-Location arcgis-mcp-service
python -m pip install -e .
```

确认解释器与服务可以启动：

```powershell
python -c "import arcpy; print(arcpy.GetInstallInfo()['Version'])"
python -m arcgis_pro_mcp
```

服务使用 stdio 传输。手动启动主要用于排错；正常情况下由 MCP 客户端拉起。

### 2. 配置 MCP 客户端

下面是通用 JSON 配置示例，可用于 Cursor、Claude Desktop 等采用 `mcpServers` 格式的客户端。把解释器、仓库和数据目录替换成自己的绝对路径：

```json
{
  "mcpServers": {
    "arcgis-pro": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": ["-m", "arcgis_pro_mcp"],
      "cwd": "C:\\path\\to\\arcgis-mcp-service",
      "env": {
        "ARCGIS_PRO_MCP_ALLOW_WRITE": "0",
        "ARCGIS_PRO_MCP_INPUT_ROOTS": "C:\\GIS_Data;D:\\Shared_GIS",
        "ARCGIS_PRO_MCP_PROJECT_ROOTS": "C:\\GIS_Projects",
        "ARCGIS_PRO_MCP_EXPORT_ROOT": "C:\\GIS_Outputs",
        "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": "C:\\GIS_Outputs\\GP",
        "ARCGIS_PRO_MCP_ENABLE_GENERIC_GP": "0"
      }
    }
  }
}
```

示例默认只读。修改配置后必须重启 MCP 客户端；不要把示例目录或本机凭据提交到公开仓库。

### 3. 先做能力探测

连接成功后，任何真实任务都应从下面两个只读工具开始：

```text
arcgis_pro_environment_info()
arcgis_pro_server_capabilities()
```

它们用于确认：

- 当前进程是否确实使用 ArcGIS Pro Python；
- 是否允许写入；
- 输入、工程、导出和 GP 输出根目录；
- 通用 GP 是否开启及其精确 allowlist；
- 当前服务实际暴露的工具类别。

文件模式随后可以这样开始：

```text
arcgis_pro_project_summary(aprx_path="C:\\GIS_Projects\\demo.aprx")
arcgis_pro_list_maps(aprx_path="C:\\GIS_Projects\\demo.aprx")
arcgis_pro_list_layers(aprx_path="C:\\GIS_Projects\\demo.aprx", map_name="Map")
```

如果 `arcgis_pro_list_projects()` 返回 `project_count: 0`，请阅读响应中的 `note`：通常需要配置工程/输入根目录，或者直接传入允许范围内的绝对 `.aprx` 路径。

## 接入当前 ArcGIS Pro 窗口

默认 stdio 服务是独立进程，不会改变已经打开的 Pro 窗口。要让 Agent 操作当前工程，需要同时运行仓库内的窗口宿主。

### 推荐启动流程

1. 在 ArcGIS Pro 中打开目标工程并先保存。Untitled/未保存工程没有稳定路径，宿主会拒绝进入 ready 状态。
2. 在 Catalog 中添加仓库根目录的 `接入当前窗口.pyt`。
3. 运行工具箱中的“接入当前窗口”，并保持它运行。
4. 调用 `arcgis_pro_window_status()`。
5. 确认响应中的 `window_attached=true`、`host_ready=true`、`target_confirmed=true`，并核对 `current_project`。
6. 使用 `arcgis_pro_active_view_info(aprx_path="CURRENT")` 做只读 smoke test。
7. 只有确实要操作眼前窗口时，才给后续工具传入 `aprx_path="CURRENT"`。

也可以在 ArcGIS Pro 的“视图 → Python 窗口”运行：

```python
import runpy
runpy.run_path(r"C:\path\to\arcgis-mcp-service\接入当前窗口.py")
```

### 一个完整的窗口工作流

```text
arcgis_pro_window_status()
  ↓ 核对 current_project / session / ready
arcgis_pro_active_view_info(aprx_path="CURRENT")
  ↓ 确认当前地图或布局
arcgis_pro_list_layers(aprx_path="CURRENT", map_name="Map")
  ↓ 使用准确 long_name 或 URI 定位图层
arcgis_pro_select_layer_by_attribute(
    aprx_path="CURRENT",
    map_name="Map",
    layer_name="Roads",
    selection_type="NEW_SELECTION",
    where_clause="OBJECTID = 1"
)
  ↓ 要求 selection_verified=true，并检查 selected_count
重新读取图层、选择集或活动视图
```

属性选择和空间选择会返回：

- `selected_count`：从真实选择集读取的准确数量；
- `result_count`：ArcPy GP Result 中的派生数量；
- `selection_verified`：两者可比较时是否一致，成功响应为 `true`；
- `ui_refresh_requested`：是否已向当前窗口请求图层刷新。

若选择数量不一致、调用在执行阶段超时，或刷新失败，结果可能已经部分生效。此时应重新读取状态，不能自动重放写请求。

### 停止和更新窗口宿主

可以取消 `.pyt`、在 Python 窗口按 Ctrl+C，或调用 `arcgis_pro_detach_window()`。

更新仓库代码后：

1. 先停止旧宿主；
2. 等待 Pro 输出“窗口宿主已停止”；
3. 在 Catalog 刷新或重新添加 `接入当前窗口.pyt`，再启动；
4. 重启 MCP 客户端；
5. 重新调用 `arcgis_pro_window_status()` 并确认目标工程。

宿主重启、同端口换成另一个 Pro 实例或当前工程切换后，旧的目标确认会失效。`CURRENT` 调用将失败关闭，不会静默接管新目标，也不会回退到文件模式。

## 安全与路径策略

所有安全策略都由启动 stdio MCP 服务的环境控制，并按调用转发给窗口宿主。窗口宿主不会自行开启写权限或创造输出目录。

| 环境变量 | 默认行为 | 作用 |
| --- | --- | --- |
| `ARCGIS_PRO_MCP_ALLOW_WRITE` | 关闭 | 设为 `1`、`true`、`yes` 或 `on` 后，才允许保存、修改、选择、写数据或运行写入型 GP。 |
| `ARCGIS_PRO_MCP_INPUT_ROOTS` | 不限制 | 可选的输入根目录；Windows 下多个目录用 `;` 分隔。 |
| `ARCGIS_PRO_MCP_PROJECT_ROOTS` | 回退到输入根目录 | 限制可打开的 `.aprx` 所在目录。 |
| `ARCGIS_PRO_MCP_EXPORT_ROOT` | 不限制 | 设置后，导出与 `saveACopy` 只能写入该目录。 |
| `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` | 未配置 | 多数写入型 GP 必须配置，且输出必须位于该目录。 |
| `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP` | 关闭 | 是否启用通用 `arcgis_pro_gp_run_tool`。 |
| `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST` | 空 | 通用 GP 的精确工具名单，如 `management.CopyFeatures,analysis.Buffer`。 |
| `ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD` | 关闭 | 是否允许通过 MCP 参数直接传数据库密码；不建议在共享环境中开启。 |
| `ARCGIS_PRO_MCP_HOST_PORT` | `17865` | 窗口宿主端口；控制多个 Pro 实例时，每组 Pro/MCP 客户端必须使用不同端口。 |

推荐的写入顺序：

1. 用 `arcgis_pro_server_capabilities()` 确认开关与根目录；
2. 用只读工具确认工程、地图、图层和数据集；
3. 调用最小、最具体的写入工具；
4. 重新读取修改后的对象；
5. 只有用户明确要求时才保存工程，优先保存副本或写入受控输出目录。

通用 GP 默认关闭。优先使用 `arcgis_pro_gp_buffer`、`arcgis_pro_gp_clip`、`arcgis_pro_gp_project` 等具名工具；只有在精确工具名已加入 allowlist 时，才应启用 `arcgis_pro_gp_run_tool`。

更多细节见 [SECURITY.md](SECURITY.md) 和 [安全与路径参考](skills/arcgis-pro-mcp/references/security-and-paths.md)。安全问题请使用 GitHub 私密漏洞报告，不要在公开 Issue 中披露凭据或漏洞细节。

## 能力范围

下面只列出代表性能力，避免 README 变成会迅速过期的 217 项工具清单。完整工具名见 [工具参考](skills/arcgis-pro-mcp/references/tools.md)，运行时以 `arcgis_pro_server_capabilities()` 为准。

| 类别 | 代表性能力 |
| --- | --- |
| 环境与窗口 | 环境探测、能力清单、窗口状态、活动视图、打开/关闭地图与布局视图、相机范围、图层刷新 |
| 工程与地图 | 工程摘要、地图/布局/报表/书签、空间参考、工程连接、创建/复制/重命名/删除地图与布局 |
| 图层与表 | 图层/表清单、可见性、透明度、比例尺、定义查询、选择集、分组与移动、数据源修复、Join |
| 布局与导出 | 地图框、图例、文本/元素位置与可见性、布局 PDF/图片、地图图片、报表 PDF |
| 数据访问 | 字段与数据集描述、表抽样、条件查询、去重值、受约束的插入/更新/删除 |
| 符号与标注 | 简单/唯一值/分级/热力渲染、套用图层符号、CIM 更新、标注表达式与字体 |
| 矢量与表 GP | Buffer、Clip、Dissolve、Intersect、Union、Erase、Spatial Join、Near、Statistics、字段计算 |
| 栅格 GP | Slope、Aspect、Hillshade、Reclassify、Extract、IDW、Kriging、Kernel Density、Raster Calculator |
| 空间统计 | Hot Spot、Local/Global Moran、Ripley's K、Nearest Neighbor、OLS、GWR、Forest、中心与方向分布 |
| 网络分析 | Route、Add Locations、Solve、Service Area、OD Matrix |
| 元数据与连接 | 读写元数据、数据库连接、SDE 数据集、工作空间与域发现 |

当前明确不提供：

- 任意 Python/ArcPy 代码执行；
- 完整替代 ArcGIS Pro 桌面 UI；
- 任意 Ribbon、菜单或对话框点击；
- Portal 发布与共享全流程；
- Utility Network、深度学习和完整编辑事务面；
- Python 窗口宿主下用户与 Agent 的完全并发操作。

窗口宿主是一个前台、串行、受约束的 ArcPy 桥接层，适合 Agent 独占一段时间操作当前工程。需要 UI 持续响应、事件订阅、可靠取消、Undo/Redo 或多人/多控制器协调时，应使用 ArcGIS Pro SDK Add-in。设计边界与路线图见 [docs/WINDOW_CONTROL.md](docs/WINDOW_CONTROL.md)。

## 故障排查

### `No module named arcpy`

MCP 客户端使用了普通 Python。把 `command` 改为 ArcGIS Pro 自带或其克隆环境中的 `python.exe`，并先在同一解释器中验证 `import arcpy`。

### MCP 能处理文件，但当前窗口没有变化

这通常表示调用仍在文件模式。检查：

1. Pro 内的 `接入当前窗口.pyt` 或 `.py` 是否正在运行；
2. `arcgis_pro_window_status()` 是否显示 ready、confirmed 且工程正确；
3. 工具参数是否明确使用 `aprx_path="CURRENT"`，而不是磁盘路径；
4. 当前活动视图是否是预期地图/布局；
5. 选择响应是否包含 `selection_verified=true` 和正确的 `selected_count`。

### `cannot import name 'FORWARDED_ENV_KEYS'`

ArcGIS Pro 长进程仍保留旧版工具箱或 Python 模块：

1. 取消旧宿主并等待“窗口宿主已停止”；
2. 在 Catalog 刷新 `接入当前窗口.pyt`，必要时移除后重新添加；
3. 或先通过 Python 窗口运行 `接入当前窗口.py`；
4. 重启 MCP 客户端；
5. 若旧工具箱类或宿主标记仍未释放，再重启 ArcGIS Pro。

当前启动器会通过包外 bootstrap 整体替换缓存的 `arcgis_pro_mcp.*` 模块，避免新版 `pro_host` 与旧版 `pro_attach` 混用。

### `window_attached=false` 或 `host_ready=false`

确认工程已经保存、宿主仍在运行、Pro 与 MCP 客户端使用相同的 `ARCGIS_PRO_MCP_HOST_PORT`。多开 Pro 时，每个实例必须分配不同端口。

### 工程列表为空

`arcgis_pro_list_projects()` 只扫描配置的 `ARCGIS_PRO_MCP_PROJECT_ROOTS`，未配置时可回退到输入根目录。设置根目录并重启客户端，或直接使用允许的绝对 `.aprx` 路径。

### 写入或导出被拒绝

先检查 `arcgis_pro_server_capabilities()`。常见原因是写入开关未开启、输出根目录未配置，或目标路径不在允许根目录内。不要通过放宽到磁盘根目录来绕过错误；应配置任务实际需要的最小目录。

### 调用超时

排队阶段超时的任务会尽量取消；已经进入 RUNNING 后的超时属于结果未知。先读取窗口状态和目标对象，不要自动重试写操作。

## Skill 与客户端协作

仓库内置的 canonical skill 位于 [skills/arcgis-pro-mcp/SKILL.md](skills/arcgis-pro-mcp/SKILL.md)，配套参考包括：

- [工具分组](skills/arcgis-pro-mcp/references/tools.md)
- [运行时注意事项](skills/arcgis-pro-mcp/references/runtime-notes.md)
- [安全与路径](skills/arcgis-pro-mcp/references/security-and-paths.md)
- [开发约定](skills/arcgis-pro-mcp/references/development.md)

Cursor、Grok、Codex 和 Claude 可以复用这份 skill。更改 MCP 配置、工具注册或 skill 后，需要重启对应客户端才能载入新状态。

## 开发与验证

多数单元测试会模拟 ArcPy，因此可以在普通 Python 3.10+ 环境中运行：

```powershell
python -m pip install -e ".[dev]"
ruff check arcgis_pro_mcp arcgis_pro_mcp_bootstrap.py tests "接入当前窗口.py"
python -m compileall -q arcgis_pro_mcp
python -m py_compile arcgis_pro_mcp_bootstrap.py "接入当前窗口.py"
python -m unittest discover -s tests -p "test_*.py"
```

在安装 ArcGIS Pro 的 Windows 上，再通过 Pro Python 执行同一套测试：

```powershell
& "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -m unittest discover -s tests -p "test_*.py"
```

这只能证明代码可在 Pro Python 下加载和通过模拟测试。涉及真实 `.aprx`、活动窗口、企业地理数据库、扩展许可或特定 GP 行为时，仍需在受控数据副本上做现场验证。

贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，并将重要变化同时记录到 [CHANGELOG.md](CHANGELOG.md) 与 [CHANGELOG.zh-CN.md](CHANGELOG.zh-CN.md)。

## 许可证

[MIT License](LICENSE)
