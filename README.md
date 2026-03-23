# arcgis-pro-mcp

基于 [Model Context Protocol](https://modelcontextprotocol.io) 的 ArcGIS Pro MCP 服务。

它通过 `arcpy.mp`、`arcpy.da` 和受约束的 `arcpy` 地理处理能力，为模型或 MCP 客户端提供 ArcGIS Pro 工程、地图、图层、布局、导出、表数据读写和部分 GP 自动化能力。

这个项目的目标不是覆盖 ArcGIS Pro 全部 UI 和全部 ArcPy 能力，而是提供一个更安全、更容易集成的能力子集。实际可用能力请始终以 `arcgis_pro_server_capabilities` 返回结果为准。

## 适用场景

- 读取 `.aprx` 中的地图、布局、图层、书签、表和工程连接信息
- 导出布局 PDF、布局图片、地图图片、报表 PDF
- 控制图层可见性、透明度、定义查询、选择集、标签、部分符号化
- 执行白名单 GP 工具，或通过通用 GP 入口运行已知工具箱工具
- 对表或要素类进行只读抽样、去重值扫描、条件查询
- 创建临时 feature layer / table view 供后续会话内工具链继续使用
- 创建、重命名、删除部分工程对象，如 map、layout、group layer、standalone table

## 不在当前范围内

- 完整替代 ArcGIS Pro 桌面 UI
- Portal/发布/共享工作流
- 深度学习相关工具链
- Utility Network 全量操作
- 完整编辑会话管理和复杂事务控制
- 任意不受约束的 ArcPy 执行

## 环境要求

- Windows
- 已安装 ArcGIS Pro
- 使用 ArcGIS Pro 自带 Python 运行

常见 Python 路径示例：

```text
C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe
```

## 安装与运行

```bash
pip install -e .
python -m arcgis_pro_mcp
```

## 安全与路径策略

服务内置了写入开关和路径约束，避免模型直接对任意路径和任意数据进行破坏性操作。

### 环境变量

| 变量 | 作用 |
| --- | --- |
| `ARCGIS_PRO_MCP_ALLOW_WRITE` | 设置为 `1` / `true` / `yes` / `on` 后，才允许保存工程、修改图层、做选择、写表、执行写入型 GP、修改布局等操作。 |
| `ARCGIS_PRO_MCP_EXPORT_ROOT` | 如果设置，所有导出文件和 `saveACopy` 输出路径必须落在该目录下。 |
| `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` | 写入型 GP 的输出根目录。很多 GP 输出要素类、表、栅格前都要求设置它。 |
| `ARCGIS_PRO_MCP_INPUT_ROOTS` | 可选输入根目录列表，多个路径使用 Windows 的 `;` 分隔。若设置，很多输入路径都必须位于这些根目录之一。 |

### 设计原则

- 默认尽量只读
- 写入必须显式开启
- 导出路径可限制到固定目录
- GP 输出可限制到固定目录
- 输入路径可限制到固定目录集合

## 启动后先做什么

建议客户端连接成功后，先调用这两个工具：

- `arcgis_pro_environment_info`
- `arcgis_pro_server_capabilities`

这样可以立刻知道：

- 当前是否真的运行在 ArcGIS Pro 自带 Python 中
- 是否允许写入
- 导出根目录是否配置
- GP 输出根目录是否配置
- 当前服务暴露了哪些工具类别

## 工具概览

下面是当前服务的高层分类。完整工具名请以 `arcgis_pro_server_capabilities` 为准。

### 1. 基础与能力探测

- `arcgis_pro_environment_info`
- `arcgis_pro_server_capabilities`

### 2. 工程、地图、图层、布局只读

- `arcgis_pro_list_maps`
- `arcgis_pro_list_layouts`
- `arcgis_pro_list_reports`
- `arcgis_pro_project_connections`
- `arcgis_pro_project_summary`
- `arcgis_pro_list_layers`
- `arcgis_pro_list_tables`
- `arcgis_pro_list_bookmarks`
- `arcgis_pro_map_spatial_reference`
- `arcgis_pro_map_camera`
- `arcgis_pro_mapframe_extent`
- `arcgis_pro_layer_properties`
- `arcgis_pro_list_layout_elements`
- `arcgis_pro_list_layout_map_frames`
- `arcgis_pro_list_broken_sources`

### 3. 数据集与工作空间只读

- `arcgis_pro_describe`
- `arcgis_pro_list_fields`
- `arcgis_pro_workspace_list_feature_classes`
- `arcgis_pro_workspace_list_rasters`
- `arcgis_pro_workspace_list_tables`
- `arcgis_pro_workspace_list_datasets`
- `arcgis_pro_workspace_list_feature_datasets`
- `arcgis_pro_workspace_list_domains`
- `arcgis_pro_list_sde_datasets`

### 4. `arcpy.da` 读写

只读：

- `arcgis_pro_da_table_sample`
- `arcgis_pro_da_query_rows`
- `arcgis_pro_da_distinct_values`

写入：

- `arcgis_pro_da_update_field_constant`
- `arcgis_pro_da_insert_features`
- `arcgis_pro_da_update_features`
- `arcgis_pro_da_delete_selected`

### 5. 地图与图层写操作

- `arcgis_pro_save_project`
- `arcgis_pro_save_project_copy`
- `arcgis_pro_set_layer_visible`
- `arcgis_pro_set_layer_transparency`
- `arcgis_pro_set_definition_query`
- `arcgis_pro_select_layer_by_attribute`
- `arcgis_pro_select_layer_by_location`
- `arcgis_pro_clear_map_selection`
- `arcgis_pro_layer_selection_count`
- `arcgis_pro_layer_selection_fids`
- `arcgis_pro_add_layer_from_path`
- `arcgis_pro_remove_layer`
- `arcgis_pro_add_table_from_path`
- `arcgis_pro_remove_table`
- `arcgis_pro_make_feature_layer`
- `arcgis_pro_make_table_view`
- `arcgis_pro_create_group_layer`
- `arcgis_pro_move_layer`
- `arcgis_pro_rename_layer`
- `arcgis_pro_set_map_reference_scale`
- `arcgis_pro_set_map_default_camera`
- `arcgis_pro_create_map`
- `arcgis_pro_duplicate_map`
- `arcgis_pro_remove_map`
- `arcgis_pro_rename_map`
- `arcgis_pro_add_basemap`

### 6. 布局、地图框、导出

- `arcgis_pro_export_layout_pdf`
- `arcgis_pro_export_layout_image`
- `arcgis_pro_export_report_pdf`
- `arcgis_pro_export_map_to_image`
- `arcgis_pro_update_layout_text_element`
- `arcgis_pro_set_mapframe_extent`
- `arcgis_pro_mapframe_zoom_to_bookmark`
- `arcgis_pro_map_pan_to_extent`
- `arcgis_pro_create_layout`
- `arcgis_pro_rename_layout`
- `arcgis_pro_set_layout_element_position`
- `arcgis_pro_set_layout_element_visible`
- `arcgis_pro_update_legend_items`

### 7. 数据源、元数据、连接

- `arcgis_pro_layer_replace_data_source`
- `arcgis_pro_repair_layer_source`
- `arcgis_pro_create_db_connection`
- `arcgis_pro_get_metadata`
- `arcgis_pro_set_metadata`

### 8. 符号化与标签

- `arcgis_pro_apply_symbology_from_layer`
- `arcgis_pro_set_unique_value_renderer`
- `arcgis_pro_set_graduated_colors_renderer`
- `arcgis_pro_set_graduated_symbols_renderer`
- `arcgis_pro_set_simple_renderer`
- `arcgis_pro_set_heatmap_renderer`
- `arcgis_pro_update_label_expression`
- `arcgis_pro_set_label_font`
- `arcgis_pro_toggle_layer_labels`
- `arcgis_pro_list_layer_renderers`
- `arcgis_pro_update_layer_cim`

### 9. GP 工具

白名单与查询类：

- `arcgis_pro_gp_list_registered`
- `arcgis_pro_gp_get_count`
- `arcgis_pro_gp_get_raster_property`
- `arcgis_pro_gp_get_cell_value`
- `arcgis_pro_gp_test_schema_lock`

常用矢量、表、转换、栅格、网络分析：

- `arcgis_pro_gp_buffer`
- `arcgis_pro_gp_clip`
- `arcgis_pro_gp_analysis_select`
- `arcgis_pro_gp_copy_features`
- `arcgis_pro_gp_table_to_table`
- `arcgis_pro_gp_export_features`
- `arcgis_pro_gp_export_table`
- `arcgis_pro_gp_merge`
- `arcgis_pro_gp_project`
- `arcgis_pro_gp_dissolve`
- `arcgis_pro_gp_intersect`
- `arcgis_pro_gp_union`
- `arcgis_pro_gp_erase`
- `arcgis_pro_gp_spatial_join`
- `arcgis_pro_gp_statistics`
- `arcgis_pro_gp_frequency`
- `arcgis_pro_gp_table_select`
- `arcgis_pro_gp_add_field`
- `arcgis_pro_gp_delete_field`
- `arcgis_pro_gp_alter_field`
- `arcgis_pro_gp_calculate_field`
- `arcgis_pro_gp_calculate_geometry`
- `arcgis_pro_gp_append`
- `arcgis_pro_gp_delete_features`
- `arcgis_pro_gp_truncate_table`
- `arcgis_pro_gp_create_feature_class`
- `arcgis_pro_gp_create_table`
- `arcgis_pro_gp_create_file_gdb`
- `arcgis_pro_gp_create_feature_dataset`
- `arcgis_pro_gp_import_csv_to_table`
- `arcgis_pro_gp_excel_to_table`
- `arcgis_pro_gp_table_to_excel`
- `arcgis_pro_gp_xy_table_to_point`
- `arcgis_pro_gp_json_to_features`
- `arcgis_pro_gp_features_to_json`
- `arcgis_pro_gp_kml_to_layer`
- `arcgis_pro_gp_feature_class_to_shapefile`
- `arcgis_pro_gp_slope`
- `arcgis_pro_gp_aspect`
- `arcgis_pro_gp_hillshade`
- `arcgis_pro_gp_reclassify`
- `arcgis_pro_gp_extract_by_mask`
- `arcgis_pro_gp_extract_by_attributes`
- `arcgis_pro_gp_raster_calculator`
- `arcgis_pro_gp_project_raster`
- `arcgis_pro_gp_validate_topology`

通用 GP 入口：

- `arcgis_pro_gp_run_tool`
- `arcgis_pro_gp_get_messages`
- `arcgis_pro_gp_list_toolboxes`
- `arcgis_pro_gp_list_tools_in_toolbox`

网络分析：

- `arcgis_pro_na_create_route_layer`
- `arcgis_pro_na_add_locations`
- `arcgis_pro_na_solve`
- `arcgis_pro_na_service_area`
- `arcgis_pro_na_od_matrix`

## 推荐使用顺序

### 只读分析

1. `arcgis_pro_environment_info`
2. `arcgis_pro_server_capabilities`
3. `arcgis_pro_project_summary`
4. `arcgis_pro_list_maps`
5. `arcgis_pro_list_layers`
6. `arcgis_pro_describe` / `arcgis_pro_list_fields`
7. `arcgis_pro_da_query_rows`

### 安全写入

1. 打开 `ARCGIS_PRO_MCP_ALLOW_WRITE`
2. 如涉及导出，设置 `ARCGIS_PRO_MCP_EXPORT_ROOT`
3. 如涉及写入型 GP，设置 `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT`
4. 先用只读工具确认对象存在
5. 再执行写入工具
6. 最后调用 `arcgis_pro_save_project`

### 临时视图工作流

1. `arcgis_pro_make_feature_layer` 或 `arcgis_pro_make_table_view`
2. 对临时层/视图继续做选择或 GP
3. 如需永久化结果，输出到受控目录

## Cursor / MCP 客户端配置示例

```json
{
  "mcpServers": {
    "arcgis-pro": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": ["-m", "arcgis_pro_mcp"],
      "cwd": "C:\\absolute\\path\\to\\arcgis-mcp-service-main",
      "env": {
        "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
        "ARCGIS_PRO_MCP_EXPORT_ROOT": "C:\\ArcGISMCP_Outputs",
        "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": "C:\\ArcGISMCP_GP",
        "ARCGIS_PRO_MCP_INPUT_ROOTS": "C:\\GIS_Data;D:\\EnterpriseGDB"
      }
    }
  }
}
```

## 版本与兼容性

- 当前包版本见 `pyproject.toml`
- README 只提供高层说明，不保证逐项穷举所有工具
- 工具面会随版本继续扩展，客户端不要把 README 当成唯一事实来源
- 可靠做法是运行时调用 `arcgis_pro_server_capabilities`

## 开发说明

```bash
pip install -e .
python -m py_compile arcgis_pro_mcp\\*.py
```

项目 CI 目前以基础安装和 `py_compile` 为主，不会在无 ArcGIS Pro 的环境中真正加载 `arcpy`。
