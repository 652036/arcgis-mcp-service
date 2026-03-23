# arcgis-mcp-service

基于 [Model Context Protocol](https://modelcontextprotocol.io) 的 ArcGIS 工具服务，通过 ArcGIS REST API 提供地理编码、逆地理编码、图层元数据与要素查询等能力。

## 依赖

- Node.js 18+

## 安装

```bash
npm install
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `ARCGIS_TOKEN` | 可选。ArcGIS Online API Key 或 OAuth `access_token`，将作为 `token` 参数附加到请求上。 |
| `ARCGIS_GEOCODE_URL` | 可选。GeocodeServer 根地址（不含尾部的 `/findAddressCandidates`）。默认使用 Esri World Geocoding。 |

## 运行

```bash
npm start
```

通过 stdio 与 MCP 宿主通信；在 Cursor / Claude Desktop 等客户端中配置为 MCP server 命令：`npx tsx` 或 `node` 指向本仓库的入口。

### Cursor 示例（`.cursor/mcp.json` 片段）

```json
{
  "mcpServers": {
    "arcgis": {
      "command": "npx",
      "args": ["tsx", "/绝对路径/到/本仓库/src/index.ts"],
      "env": {
        "ARCGIS_TOKEN": "你的令牌或留空"
      }
    }
  }
}
```

## MCP 工具一览

- `arcgis_geocode` — 正向地理编码  
- `arcgis_reverse_geocode` — 逆地理编码（WGS84 经纬度）  
- `arcgis_geocode_suggest` — 地址/地名自动补全建议  
- `arcgis_layer_metadata` — 读取图层 REST 元数据  
- `arcgis_query_layer` — 对要素图层执行 `query`（`where`、字段、分页等）
