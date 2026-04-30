# 更新日志

本文件记录该项目的重要变更。

英文版: [`CHANGELOG.md`](./CHANGELOG.md)

## [Unreleased]

### 新增
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

### 变更
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
