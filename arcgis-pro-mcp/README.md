# arcgis-pro-mcp

面向 **ArcGIS Pro（ArcPy）** 与 **ArcGIS Online / Portal Web 地图（Sharing REST）** 的 MCP 服务。

## ArcGIS Pro

必须在 **已安装 ArcGIS Pro 的 Windows** 上，使用 Pro 自带的 Python 解释器启动（该环境才包含 `arcpy`），例如：

```text
"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" -m arcgis_pro_mcp
```

## Web 地图（Portal / ArcGIS Online）

在任意能联网的环境运行即可。可选环境变量：

- `ARCGIS_PORTAL_URL`：Sharing REST 根路径，默认 `https://www.arcgis.com/sharing/rest`
- `ARCGIS_TOKEN` 或 `ARCGIS_PORTAL_TOKEN`：访问私有内容时使用

## 安装与运行

```bash
cd arcgis-pro-mcp
pip install -e .
arcgis-pro-mcp
# 或
python -m arcgis_pro_mcp
```
