# arcgis-pro-mcp

基于 [Model Context Protocol](https://modelcontextprotocol.io) 的 **ArcGIS Pro** 自动化服务：通过 **arcpy.mp** 与受控 **arcpy** 地理处理，读写工程、图层、布局与导出。

**说明：** 无法通过本服务「完全替代」ArcGIS Pro 的图形界面（菜单、窗格、交互编辑等未公开为脚本 API）。此处覆盖的是 **arcpy 能表达的工程/制图/数据自动化**；更深层能力需 Esri 插件或 Pro SDK。

## 环境要求

- Windows，已安装 **ArcGIS Pro**
- 使用 **Pro 自带的 Python**，例如：

```text
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
```

## 环境变量（安全与路径）

| 变量 | 说明 |
|------|------|
| `ARCGIS_PRO_MCP_ALLOW_WRITE` | 设为 `1` / `true` / `yes` / `on` 才允许：**保存工程、改图层、按属性选择、地图框缩放到书签、添加/移除图层**。 |
| `ARCGIS_PRO_MCP_EXPORT_ROOT` | 若设置，**所有导出与 saveACopy 的 aprx** 解析后的路径须位于该目录下。 |
| `ARCGIS_PRO_MCP_GP_OUTPUT_ROOT` | 预留：将来写入型 GP 输出路径校验（当前白名单 GP 多为只读）。 |
| `ARCGIS_PRO_MCP_INPUT_ROOTS` | 若设置，多个根目录用系统路径分隔符分隔（Windows 为 `;`），**addDataFromPath、Describe、ListFields、GetCount 等输入路径**须落在其一之下。 |

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

### 导出（须绝对路径；受 `EXPORT_ROOT` 约束）

`arcgis_pro_export_layout_pdf`、`arcgis_pro_export_layout_image`（`png` / `jpeg` / `tiff`，TIFF 可选 `world_file`）。

### 写入（须 `ALLOW_WRITE`）

`arcgis_pro_save_project`、`arcgis_pro_save_project_copy`、`arcgis_pro_set_layer_visible`、`arcgis_pro_set_layer_transparency`、`arcgis_pro_set_definition_query`、`arcgis_pro_select_layer_by_attribute`、`arcgis_pro_mapframe_zoom_to_bookmark`、`arcgis_pro_add_layer_from_path`、`arcgis_pro_remove_layer`。

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
