# 更新日志

本文件记录该项目的重要变更。

英文版: [`CHANGELOG.md`](./CHANGELOG.md)

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
