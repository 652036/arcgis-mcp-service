# arcgis-pro-mcp

基于 [Model Context Protocol](https://modelcontextprotocol.io) 的 **ArcGIS Pro** 工具服务：通过 **ArcPy** 读取本机 `.aprx` 工程中的地图、布局与图层。

## 环境要求

- Windows，已安装 **ArcGIS Pro**
- 使用 **Pro 自带的 Python**（该环境提供 `arcpy`），例如：

```text
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
```

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

## MCP 工具

| 工具 | 说明 |
|------|------|
| `arcgis_pro_list_maps` | 列出工程内所有地图名称 |
| `arcgis_pro_list_layouts` | 列出所有布局名称 |
| `arcgis_pro_list_layers` | 列出指定地图中的图层及数据源摘要 |

## Cursor 示例（`.cursor/mcp.json`）

将 `command` 指向本机 Pro 的 `python.exe`，`args` 使用本仓库的模块或已安装的包：

```json
{
  "mcpServers": {
    "arcgis-pro": {
      "command": "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe",
      "args": ["-m", "arcgis_pro_mcp"],
      "cwd": "C:\\绝对路径\\到\\本仓库"
    }
  }
}
```

若已通过 `pip install -e .` 安装到 Pro 的 Python 环境，可省略 `cwd` 或仅保留工作目录需要时的路径。
