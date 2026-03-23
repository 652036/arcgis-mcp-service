# arcgis-pro-mcp

基于 [Model Context Protocol](https://modelcontextprotocol.io) 的 **ArcGIS Pro** 自动化服务：通过 **arcpy.mp**、**arcpy.da（只读）** 与受控 **arcpy** 地理处理，读写工程、图层、布局与数据抽样。

**说明：** 「ArcGIS Pro 全功能清单」级别的能力不可能在单一 MCP 内全部实现；本仓库仅提供 **安全子集**。无法替代完整 Pro UI；发布/深度学习/Utility Network/完整编辑等多数模块未封装。实际能力以 **`arcgis_pro_server_capabilities`** 为准。

## 环境要求

- Windows，已安装 **ArcGIS Pro**
- 使用 **Pro 自带的 Python**，例如：

```text
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
```

## 环境变量（安全与路径）

| 变量 | 说明 |
|------|------|
| `ARCGIS_PRO_MCP_ALLOW_WRITE` | 设为 `1` / `true` / `yes` / `on` 才允许：**保存工程、改图层/Join、参考比例尺与 Camera、按属性/位置选择、清除选择、写入型 GP（Buffer/Clip/Select/CopyFeatures）、布局文本与地图框范围** 等。 |
| `ARCGIS_PRO_MCP_EXPORT_ROOT` | 若设置，**所有导出与 saveACopy 的 aprx** 解析后的路径须位于该目录下。 |
| `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` | **必填**（若使用 `arcgis_pro_gp_buffer` / `gp_clip` / `gp_analysis_select` / `gp_copy_features`）：输出要素类路径须位于该绝对根目录下。 |
| `ARCGIS_PRO_MCP_INPUT_ROOTS` | 若设置，多个根目录用系统路径分隔符分隔（Windows 为 `;`），**工作空间枚举、da 抽样、addDataFromPath、Describe、ListFields、白名单 GP 输入路径**等须落在其一之下。 |

## 安装与运行

```bash
pip install -e .
python -m arcgis_pro_mcp
```

## 能力探测

调用 **`arcgis_pro_server_capabilities`** 可查看当前是否允许写入、路径策略是否启用，以及工具分类列表。

## 工具概览

### 只读 / 元数据

`arcgis_pro_environment_info`、`arcgis_pro_server_capabilities`、`arcgis_pro_describe`、`arcgis_pro_list_fields`、`arcgis_pro_project_connections`、`arcgis_pro_project_summary`、各类 `list_*`、`map_*` 读取、`mapframe_extent`、`layer_properties`、增强后的 **`arcgis_pro_list_bookmarks`**（含 description、has_thumbnail、map_name）。

**工作空间枚举：** `arcgis_pro_workspace_list_feature_classes`、`arcgis_pro_workspace_list_rasters`、`arcgis_pro_workspace_list_tables`。

**属性表只读抽样：** `arcgis_pro_da_table_sample`、`arcgis_pro_da_distinct_values`。

**白名单 GP（只读类）：** `arcgis_pro_gp_get_count`、`arcgis_pro_gp_get_raster_property`、`arcgis_pro_gp_get_cell_value`、`arcgis_pro_gp_test_schema_lock`、`arcgis_pro_gp_list_registered`。

**白名单 GP（写入类，须同时 `ALLOW_WRITE` + `GP_OUTPUT_ROOT`）：**  
`arcgis_pro_gp_buffer`、`arcgis_pro_gp_clip`、`arcgis_pro_gp_analysis_select`、`arcgis_pro_gp_copy_features`、`arcgis_pro_gp_dissolve`、`arcgis_pro_gp_intersect`、`arcgis_pro_gp_union`、`arcgis_pro_gp_erase`、`arcgis_pro_gp_spatial_join`、`arcgis_pro_gp_statistics`、`arcgis_pro_gp_frequency`、`arcgis_pro_gp_table_select`、`arcgis_pro_gp_merge`、`arcgis_pro_gp_project`。

**地图/图层制图：** `arcgis_pro_set_map_spatial_reference`、`arcgis_pro_layer_replace_data_source`、`arcgis_pro_apply_symbology_from_layer`、`arcgis_pro_set_layer_scale_range`、`arcgis_pro_toggle_layer_labels`。

**选择集：** `arcgis_pro_select_layer_by_location`、`arcgis_pro_clear_map_selection`；只读：`arcgis_pro_layer_selection_count`、`arcgis_pro_layer_selection_fids`。

**连接：** `arcgis_pro_add_join`、`arcgis_pro_remove_join`。

**布局写入：** `arcgis_pro_update_layout_text_element`、`arcgis_pro_set_mapframe_extent`。

### 导出（须绝对路径；受 `EXPORT_ROOT` 约束）

`arcgis_pro_export_layout_pdf`、`arcgis_pro_export_layout_image`（`png` / `jpeg` / `tiff`，TIFF 可选 `world_file`）。

### 写入（须 `ALLOW_WRITE`）

`arcgis_pro_save_project`、`arcgis_pro_save_project_copy`、`arcgis_pro_set_layer_visible`、`arcgis_pro_set_layer_transparency`、`arcgis_pro_set_definition_query`、`arcgis_pro_select_layer_by_attribute`、`arcgis_pro_mapframe_zoom_to_bookmark`、`arcgis_pro_add_layer_from_path`、`arcgis_pro_remove_layer`、`arcgis_pro_create_group_layer`、`arcgis_pro_move_layer`、`arcgis_pro_rename_layer`、`arcgis_pro_set_map_reference_scale`、`arcgis_pro_set_map_default_camera`。

### 白名单地理处理

`arcgis_pro_gp_list_registered`、`arcgis_pro_gp_get_count`、`arcgis_pro_gp_get_raster_property`（`property_type` 已枚举限制）。

## Cursor 示例

```json
{
  "mcpServers": {
    "arcgis-pro": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": ["-m", "arcgis_pro_mcp"],
      "cwd": "C:\\绝对路径\\到\\本仓库",
      "env": {
        "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
        "ARCGIS_PRO_MCP_EXPORT_ROOT": "C:\\ArcGISMCP_Outputs",
        "ARCGIS_PRO_MCP_INPUT_ROOTS": "C:\\GIS_Data;D:\\EnterpriseGDB"
      }
    }
  }
}
```

## CI

GitHub Actions 在 Ubuntu 上 `pip install -e .` 并对 `arcgis_pro_mcp` 下模块做 `py_compile`（不加载 `arcpy`）。
