# arcgis-mcp-service

本仓库提供 **两个** 独立的 MCP 服务，可按需选用。

---

## 1. ArcGIS REST（Node / TypeScript）

面向 **GeocodeServer** 与 **要素图层 REST**（与是否安装 ArcGIS Pro 无关）：地理编码、图层 `query`、元数据等。

**依赖：** Node.js 18+  

```bash
npm install
npm start
```

| 环境变量 | 说明 |
|----------|------|
| `ARCGIS_TOKEN` | 可选，作为 `token` 附加到 REST 请求 |
| `ARCGIS_GEOCODE_URL` | 可选，GeocodeServer 根地址；默认 World Geocoding |

**工具：** `arcgis_geocode`、`arcgis_reverse_geocode`、`arcgis_geocode_suggest`、`arcgis_layer_metadata`、`arcgis_query_layer`

---

## 2. ArcGIS Pro / Web 地图（Python）

目录：`arcgis-pro-mcp/`

- **ArcGIS Pro**：通过 **ArcPy** 读取本机 `.aprx` 中的地图、布局、图层（**必须在已安装 ArcGIS Pro 的 Windows 上，用 Pro 自带的 Python 启动**）。
- **Map（Web Map）**：通过 **ArcGIS Online / Portal Sharing REST** 拉取 Web Map JSON、条目信息与搜索（任意平台，需网络；私有内容配 token）。

```bash
cd arcgis-pro-mcp
pip install -e .
python -m arcgis_pro_mcp
```

| 环境变量 | 说明 |
|----------|------|
| `ARCGIS_PORTAL_URL` | 可选，默认 `https://www.arcgis.com/sharing/rest` |
| `ARCGIS_TOKEN` / `ARCGIS_PORTAL_TOKEN` | 可选，访问私有 Portal 内容 |

**Pro（ArcPy）工具：** `arcgis_pro_list_maps`、`arcgis_pro_list_layouts`、`arcgis_pro_list_layers`  

**Portal / Web Map 工具：** `arcgis_portal_webmap_json`、`arcgis_portal_item_metadata`、`arcgis_portal_search_items`

详见 `arcgis-pro-mcp/README.md`。

---

## Cursor 配置示例

**REST（TypeScript）：**

```json
{
  "mcpServers": {
    "arcgis-rest": {
      "command": "npx",
      "args": ["tsx", "/绝对路径/本仓库/src/index.ts"],
      "env": { "ARCGIS_TOKEN": "" }
    }
  }
}
```

**Pro / Web 地图（Python，Pro 用户请把 `command` 换成 Pro 自带 `python.exe`）：**

```json
{
  "mcpServers": {
    "arcgis-pro-map": {
      "command": "python3",
      "args": ["-m", "arcgis_pro_mcp"],
      "cwd": "/绝对路径/本仓库/arcgis-pro-mcp"
    }
  }
}
```
