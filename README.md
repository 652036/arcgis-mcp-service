# arcgis-pro-mcp

基于 [Model Context Protocol](https://modelcontextprotocol.io) 的 **ArcGIS Pro** 工具服务：通过 **arcpy.mp** 与 **arcpy** 读取工程、数据模式与制图属性，并支持将布局导出为 PDF。

## 环境要求

- Windows，已安装 **ArcGIS Pro**
- 使用 **Pro 自带的 Python**（该环境提供 `arcpy`），例如：

```text
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
```

## 可选环境变量

| 变量 | 说明 |
|------|------|
| `ARCGIS_PRO_MCP_EXPORT_ROOT` | 若设置，`arcgis_pro_export_layout_pdf` 的 `output_pdf_path` 解析后（`realpath`）必须位于该目录下，用于限制 PDF 导出位置。 |

## 安装

```bash
pip install -e .
```

## 运行

```bash
arcgis-pro-mcp
# 或
python -m arcgis_pro_mcp
```

务必用上面的 Pro 解释器执行，否则无法导入 `arcpy`。

## MCP 工具（与 Pro 制图工程对应）

| 工具 | 说明 |
|------|------|
| `arcgis_pro_environment_info` | 当前 ArcPy / Pro 安装与产品信息（无需 .aprx） |
| `arcgis_pro_describe` | 对数据集/数据源路径执行 `Describe`（类型、范围、空间参考等） |
| `arcgis_pro_list_fields` | 表或要素类字段列表（`ListFields`） |
| `arcgis_pro_project_connections` | 工程中的文件夹连接、数据库、工具箱等 |
| `arcgis_pro_project_summary` | 工程概览：地图/布局/报表、损坏数据源抽样 |
| `arcgis_pro_list_maps` | 列出所有地图 |
| `arcgis_pro_list_layouts` | 列出所有布局 |
| `arcgis_pro_list_reports` | 列出报表（若当前 Pro 版本支持） |
| `arcgis_pro_list_layers` | 列出地图中的图层（含要素/栅格标识与数据源） |
| `arcgis_pro_list_tables` | 列出地图中的表 |
| `arcgis_pro_map_spatial_reference` | 地图空间参考 |
| `arcgis_pro_map_camera` | 地图默认 Camera（比例尺、范围等，若可读取） |
| `arcgis_pro_list_bookmarks` | 地图书签 |
| `arcgis_pro_layer_properties` | 单个图层：定义查询、符号类型、透明度等 |
| `arcgis_pro_list_layout_elements` | 布局元素类型与名称（可选按元素类型过滤） |
| `arcgis_pro_mapframe_extent` | 布局中地图框的范围、比例尺、关联地图 |
| `arcgis_pro_export_layout_pdf` | 将布局导出为 PDF（**须使用绝对路径**；见 `ARCGIS_PRO_MCP_EXPORT_ROOT`） |

## Cursor 示例（`.cursor/mcp.json`）

将 `command` 指向本机 Pro 的 `python.exe`，`args` 使用本仓库的模块或已安装的包：

```json
{
  "mcpServers": {
    "arcgis-pro": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": ["-m", "arcgis_pro_mcp"],
      "cwd": "C:\\绝对路径\\到\\本仓库",
      "env": {
        "ARCGIS_PRO_MCP_EXPORT_ROOT": "C:\\导出目录"
      }
    }
  }
}
```

若已通过 `pip install -e .` 安装到 Pro 的 Python 环境，可省略 `cwd` 或仅保留工作目录需要时的路径。

## 说明

- 工具覆盖 **工程 / 数据描述 / 地图 / 图层 / 布局 / 地图框 / 导出** 等常见制图工作流；**不包含**任意地理处理（GP）工具箱的开放执行，以降低误操作风险。
- 导出 PDF 会写磁盘；`output_pdf_path` 必须为**绝对路径**。若 Pro 正独占打开同一 `.aprx`，请先确认无冲突。

## CI

GitHub Actions 在 Ubuntu 上执行 `pip install -e .` 与 `py_compile`（不加载 `arcpy`）。
