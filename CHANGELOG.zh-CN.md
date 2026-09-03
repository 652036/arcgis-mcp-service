# 更新日志

本文件记录该项目的重要变更。

英文版: [`CHANGELOG.md`](./CHANGELOG.md)

## [Unreleased]

## [2.0.0] - 2026-09-03

### 新增
- 新增第三种可选原生控制面 `sdk/ArcGISProMcp.AddIn`：仅 loopback 发现、
  每次加载随机凭据、独占短工程租约、活动上下文 generations、仅元数据事件、
  相机/时间/表窗格命令、可确认 DrawComplete 的刷新、原生 `EditOperation`
  要素写入、Undo/Redo/保存/丢弃，以及可取消的 typed GP 作业。Python MCP
  封装不会向模型暴露 bearer 或 lease secret。
- 受控 MCP 工具面扩展到 400+，并改为运行时动态编目。新增聚焦模块覆盖工程
  导入/导出与连接修复、制图与图表、数据集/方案维护、属性完整性、符号系统、
  栅格/地图代数/水文/镶嵌、LAS、高级空间与时空建模、本地地理编码、本地网络
  分析、企业版本化、Utility Network 工作流和受门禁保护的发布。
- 新增 `arcgis_pro_tool_info` 和机器可读的逐工具策略目录，无需依赖静态工具数，
  即可查询读写分类、路径根、窗口要求、确认参数和附加门禁。
- 新增 Python 实时窗口 job 提交/状态/取消与有界变化等待，同时保持精确
  `CURRENT` 目标绑定。
- 窗口接入：运行 `接入当前窗口.pyt` 或在 ArcGIS Pro Python 窗口运行
  `接入当前窗口.py` 后，显式使用 `aprx_path=CURRENT` 的调用在当前工程执行。
  新增活动视图状态、打开/关闭地图与布局视图、实时范围/图层缩放和图层刷新工具。
- 窗口协议加入每会话令牌、协议/包版本、目标工程与 session 校验、原子状态文件、
  readiness/忙碌/队列诊断、有界任务队列、排队超时取消、停止/接收竞态保护，
  为显式配置的宿主端口隔离状态文件，并用窗口状态确认锁存 session/project，
  防止宿主重启或同端口换窗口后静默改写目标。
- 新增面向科研的空间统计模块（`arcgis_pro_mcp/gp_stats.py`），注册 14 个新 MCP
  工具：Getis-Ord Gi* 热点分析、优化热点分析、Anselin Local Moran's I 聚类与异常、
  Ripley's K 多距离空间聚类、全局 Moran's I 空间自相关、平均最近邻、OLS 普通最小二乘、
  GWR 地理加权回归、基于森林的分类与回归（训练）、中心要素、平均中心、方向分布
  （标准差椭圆）、创建随机点、生成镶嵌格网（蜂窝/网格）。诊断类工具返回地理处理
  消息以便记录统计指标；所有产出型工具沿用现有写入开关与 GP 输出根目录策略。
- 新增仓库内 Codex skill：`skills/arcgis-pro-mcp/SKILL.md`，并配套
  ArcGIS Pro 运行要求、写入/路径安全开关、MCP 工具分组与开发说明参考文档。
- 新增 `AGENTS.md` 与 `CLAUDE.md`，用于项目级 Agent 协作说明。
- 新增 `LICENSE`（MIT），并补齐 `pyproject.toml` 的包元信息（license、项目
  URL、classifiers、keywords、`dev` extras）。
- 新增 `py.typed` 标记文件，使下游类型检查器能使用包中的类型注解。
- 新增 `SECURITY.md`、`CONTRIBUTING.md`、Pull Request 模板、Bug/功能请求 Issue
  模板，以及针对 pip 与 GitHub Actions 的 `dependabot.yml`（每周更新）。
- 在 `arcgis_pro_mcp.__main__` 中新增顶层异常兜底，启动失败时输出可读提示，
  而不是裸异常堆栈。

### 修复
- 收紧独立通用 GP 与 CURRENT 地图分析：每次调用必须在已配置的 GP 输出根下
  创建至少一个完整目标路径；两者拒绝已有目标并强制 `overwriteOutput=False`。
  即使工具已在 allowlist 中，输出容器/名称分离、原地或无输出、破坏性和代码
  执行类操作仍会被拒绝。
- Calculate Field 仅接受经校验的纯 Arcade 表达式子集，标注表达式仅允许
  Arcade，不再暴露 Python/VB/code block 执行路径。Repair Geometry 固定使用
  `KEEP_NULL`，不会把删除空几何记录当作修复副作用。
- 地图、布局、报表、图表、工程副本以及本地发布草稿/服务定义导出现在会拒绝
  已存在路径，不再继承 ArcPy 的覆盖状态。
- Python CURRENT 发现状态迁移到当前用户私有的
  `%LOCALAPPDATA%\ArcGISProMcp\window-host`，使用原子发布、受保护 ACL/所有者
  校验、有界读取，并拒绝链接/reparse point。
- 创建数据库连接现在只允许精确白名单实例并只读取固定专用凭据变量，禁止由调用方
  指定任意环境变量或把凭据发送到任意目标；新连接文件拒绝覆盖，且默认不保存凭据。
- 镶嵌数据集 `OVERWRITE_DUPLICATES`，以及可能丢弃未保存缓存状态的文件模式工程
  release/reload，现在都要求破坏性门禁和相应精确确认。
- 选择写操作现在会将 ArcPy 派生计数与图层真实 `getSelectionSet()` 交叉核验后再
  报告成功，在实时窗口中主动请求重绘，并返回准确的选择数量/FID。图层属性改为
  检查 `Symbology.renderer` 或 `Symbology.colorizer`，不再访问不存在的
  `Symbology.type`。这修复了 Issue #6 中的假成功、窗口未同步、计数错误和符号系统报错。
- 窗口入口不再只刷新 `pro_host`、却继续沿用 ArcGIS Pro 进程内的旧模块缓存。
  现在由包外 bootstrap 在旧宿主停止后整代替换 `arcgis_pro_mcp.*`，避免新版宿主
  混用旧版 `pro_attach`、辅助模块或 FastMCP 工具注册，并修复旧代模块导致的
  `FORWARDED_ENV_KEYS` 导入失败。
- 窗口宿主不再强制开启写入或硬编码本机驱动器路径；它按调用沿用 stdio MCP 的
  allowlist 安全策略。绝对 `.aprx` 不再被静默改写为 `CURRENT`，CURRENT 失联或
  工程切换时会失败关闭。每个窗口请求只使用一个绑定的 CURRENT 工程引用。
- `arcgis_pro_list_projects` 在未配置工程/输入根目录时改为返回空清单与说明，不再抛错。
- `remove_map` / `remove_layout` 改为调用 `ArcGISProject.deleteItem`。
- 地图导出改为使用 `Map.defaultView`；热力图改为 CIM 渲染器，不再调用无效的 `HeatMapRenderer`。
- 标注表达式引擎与字体改走 CIM；`zoom_to_selection` 按选择集几何计算范围。
- DA 插入仅允许 `SHAPE@WKT` / `SHAPE@JSON` 字符串；更新禁止改 OID。
- 创建数据库连接时确保输出文件夹存在；SDE 列表包含要素数据集内的要素类。
- Dissolve 改为 `management.Dissolve`；方案锁改为 `arcpy.TestSchemaLock`；
  ExportFeatures/ExportTable 在 Pro 3.6 上回退到 Conversion 工具箱。
- Kriging 补上普通克里金模型；Ripley's K 默认 envelope 与工具域一致；
  地图框范围改走 `camera.setExtent`；图层数据源改走 `updateConnectionProperties`。
- `arcgis_pro_gp_import_csv_to_table` 不再与 `arcgis_pro_gp_table_to_table`
  完全重复。改为调用专用的 `run_import_csv_to_table`，校验输入为分隔文本文件
  （`.csv`/`.txt`/`.tab`），并使用语义更清晰的 `in_csv` 参数名。

### 变更
- 统一并澄清门禁元数据：enterprise-write 仅保护版本管理、维护和 Utility
  Network 管理；普通要素/行编辑使用 WRITE，并在适用时叠加 destructive 与
  SDK feature gate。
- 围绕三个显式执行面重写或更新 README、实时窗口架构、贡献指南、安全文档和
  共享 Agent skill：文件模式、Python `CURRENT` 协议 v4、SDK Add-In。
- skill 现同时面向 Cursor、Grok、Codex、Claude，并新增 Pro 3.6 运行时说明
  `skills/arcgis-pro-mcp/references/runtime-notes.md`。
- 将 `mcp` 依赖收紧为 `>=1.20,<2`，避免未来主版本变更导致悄悄断裂。
- CI 扩展为 Ubuntu + Windows 的 Python 3.10 / 3.11 / 3.12 矩阵，并新增独立
  的 ruff lint 任务。

### 破坏性变更
- `arcgis_pro_gp_eliminate` 参数变更：移除 `selection_type`，改为 `condition`（`AREA`/`PERCENT`/`AREA_OR_PERCENT`）、`part_area`、`part_area_percent`、`part_option`。
- `arcgis_pro_da_update_features` 移除从未生效的 `field_name` 参数，现有调用需删除该实参。
- `da_write.insert_features` 移除从未生效的 `include_geometry_wkt` 参数；几何插入一直依赖在 `fields` 中加入 `SHAPE@WKT`。

### 修复
- 修复 `arcgis_pro_gp_eliminate` 参数与底层 `EliminatePolygonPart` 不匹配的问题。原参数 `selection_type=LENGTH/AREA` 在运行时会直接报错。
- 修复 `arcgis_pro_zoom_to_selection` 不再忽略 `layer_name`，现在会按指定图层（含选择集）的范围设置地图框，而不是缩放到所有图层。
- 将服务端所有残留的 `Invalid arguments` 占位错误（selection / placement / overlap / join 枚举校验，以及 map frame / layout element / legend / text element 查找）全部替换为包含具体取值与合法集合或候选清单的可读消息。

### 删除
- 删除服务端遗留的死代码 `_query_rows` / `_sanitize_order_by` / `_MAX_QUERY_WHERE` / `_MAX_QUERY_CELL`，这些在 1.0.1 已由共享的 `da_read.query_rows` 替代。

## [1.0.1] - 2026-03-25

### 新增
- 新增 `arcgis_pro_list_projects`，用于在已配置的工程根目录下发现 `.aprx` 项目。
- 新增 `arcgis_pro_remove_layout`，补齐基础 layout 生命周期操作。
- 新增 `ARCGIS_PRO_MCP_PROJECT_ROOTS`，用于将 ArcGIS Pro 工程路径与普通数据输入路径分开约束。
- 新增 `ARCGIS_PRO_MCP_ENABLE_GENERIC_GP` 和 `ARCGIS_PRO_MCP_GENERIC_GP_ALLOWLIST`，用于显式控制通用 GP 运行器。
- 为 `arcgis_pro_create_db_connection` 新增 `username_env_var` 和 `password_env_var` 参数。
- 新增单元测试，覆盖工程路径校验、通用 GP 开关与 allowlist、共享查询委托、数据库连接凭据以及项目发现行为。

### 变更
- 通用 GP 执行现在默认禁用，必须显式开启并加入 allowlist 后才能运行。
- 通用 GP 参数处理现在会对疑似输入/输出路径执行现有 MCP 根目录策略校验。
- `.aprx` 加载现在必须使用绝对路径，并根据工程根目录或输入根目录进行校验。
- `arcgis_pro_da_query_rows` 现在复用共享的 `da_read.query_rows` 实现，不再使用重复的 server 侧版本。
- `arcgis_pro_environment_info` 和 `arcgis_pro_server_capabilities` 现在会返回工程根目录和通用 GP 配置状态。
- CI 现在改为对整个包执行 `compileall`，并运行新的单元测试集。
- README 已同步更新，补充工程根目录策略、通用 GP 开关、数据库凭据建议和新增工具说明。

### 修复
- 修复 `arcgis_pro_remove_join`，现在会正确使用 `_open_project` 返回的 `arcpy` 对象。
- 修复 `arcgis_pro_mapframe_zoom_to_bookmark`，避免书签查找失败时被 `NameError` 掩盖原始异常。
- 修复查询和 server 辅助逻辑中的多处中文乱码校验提示。
- 修复 `da_read.query_rows` 的排序逻辑，`order_by` 现在会生成正确的 `ORDER BY` SQL 片段，并拒绝换行和分号注入。
- 修复 `arcgis_pro_describe` 和 `arcgis_pro_list_fields`，使其和其他数据集只读工具一样遵守输入根目录策略。
- 改进 map、layout、layer、map frame、bookmark、table、field 等对象不存在时的报错，返回候选值而不是笼统的 `Invalid arguments`。

### 安全
- 收紧通用 GP 运行器，避免其继续作为绕过受控 MCP 工具面的非受限入口。
- 默认禁止内联数据库密码；直接传 `password` 现在需要显式设置 `ARCGIS_PRO_MCP_ALLOW_INLINE_DB_PASSWORD=1`。
