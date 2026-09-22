# 生态流程验收

[English](ECOLOGICAL_QA.md)

## 通过 MCP 调用加权叠加

`arcgis_pro_gp_run_tool` 支持 `sa.WeightedOverlay` 和 `WeightedOverlay_sa`。
`in_weighted_overlay_table` 传结构化 JSON，服务端转换成原生 ArcPy
`WOTable` / `RemapValue`，再调用注册的 GP 工具：

```json
{
  "tool_name": "sa.WeightedOverlay",
  "parameters": {
    "in_weighted_overlay_table": {
      "evaluation_scale": [1, 9, 1],
      "rasters": [
        {
          "raster": "C:/GIS/input/landuse.tif",
          "influence": 100,
          "field": "Value",
          "remap": [[1, 5], [2, 1], [5, 2], [8, 9]]
        }
      ]
    },
    "out_raster": "C:/GIS/output/resistance.tif"
  }
}
```

按实际数据和分析方案修改路径、映射。每个内嵌栅格都执行输入根校验，输出仍必须是
GP 输出根内的新路径。权重必须为整数百分比，合计 100；评价尺度为
`[最小值, 最大值, 步长]`，数值映射结果必须属于该尺度。允许显式使用原生
`NODATA` 和 `RESTRICTED`。当前适配整数值映射，不适配区间或文本属性映射。
重复输入值、未知 JSON 字段、原生表字符串均拒绝；路径中的单引号、分号和换行符
也会拒绝，避免原生复合参数无法正确引用路径。

面积制表 `TabulateArea_sa` 必须使用数据中真实存在的分区字段、类别字段。
按像元分类统计时，确认 `Value` 字段后使用它；不能凭空填写 `GROUP_ID`，也不应
在指定属性字段不存在时静默改用像元值。明确处理像元大小，并核验面积总量。

## 本机数据的可复现实测

`tests/test_ecological_workflow_native.py` 会启动配置中的 **stdio MCP 服务**，
复制本机 CLCD 土地利用 GeoTIFF 及附属文件，在全新的输出目录内执行原生 GP。
输入限投影米制、正方形像元、不超过 100 万像元。测试选择类别 2 的森林，提取两个
最大的八邻域斑块；两组映射及 60/40 权重只是明确的验收模型，不是推荐论文权重，
也不证明这些斑块具有生态源地资格。此测试不包含 MSPA、InVEST、Circuitscape 或
连通性指数模型。

流程逐步校验，前一步失败就停止：

- 源地选择、连通斑块与独立 NumPy/SciPy 结果一致；
- 阻力面全部像元、NoData 掩膜、坐标系、分辨率和网格原点一致；
- 两个成本距离结果与独立八邻域 Dijkstra 算法逐像元比较；
- 源地成本为零、廊道值为两端成本和、路径连通两端并沿回溯方向递减；
- 面积表每个分区和类别、面积总量、栅格转面面积、分区计数/均值/总和一致；
- 错误权重、缺失类别字段被真实 MCP 拒绝；
- 注入半像元错位、NoData 缺口、错误值或非正阻力时，后续 MCP 请求未发出；
- 原始输入及附属文件 SHA-256 保持不变。

在仓库根目录使用 ArcGIS Pro Python 和兼容的 MCP 依赖：

```powershell
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Resolve-Path .arcgis-pro-mcp-deps).Path
    $env:ARCGIS_MCP_RUN_ECOLOGY_QA = "1"
    $env:ARCGIS_MCP_ECOLOGY_CONFIG = (Resolve-Path .mcp.json).Path
    $env:ARCGIS_MCP_ECOLOGY_LANDUSE = "C:\GIS\input\landuse.tif"
    $env:ARCGIS_MCP_ECOLOGY_OUTPUT_ROOT = "C:\GIS\qa\new-acceptance-run"
    & "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -m unittest tests.test_ecological_workflow_native -v
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Remove-Item Env:ARCGIS_MCP_RUN_ECOLOGY_QA -ErrorAction SilentlyContinue
}
```

输出目录必须尚不存在。独立测试服务将输入和 GP 输出根限定为该目录，不改客户端
配置、原始数据或 CURRENT 工程。`acceptance.json` 保留检查项、请求、错误和运行环境；
`mcp.log` 保留原生日志。校验失败会终止流程并留下 FAILED 报告；预期拒绝案例另计为
通过的检查项。这些文件包含本机路径，不应提交到版本库。

通过只证明这组模型、数据和环境的验收要求。逐步阻断属于本验收程序，未给全部旧
MCP 工具增加统一门禁。完整生产数据、其他源地规则、阻力模型、许可和环境仍需对应
验收。已有 MCP 进程需要重启才能加载源码更新。

## 原生接口依据

- [Esri WOTable](https://pro.arcgis.com/en/pro-app/3.6/arcpy/spatial-analyst/wotable-class.htm)
- [Esri Weighted Overlay](https://pro.arcgis.com/en/pro-app/3.6/tool-reference/spatial-analyst/weighted-overlay.htm)
- [Esri Tabulate Area](https://pro.arcgis.com/en/pro-app/3.6/tool-reference/spatial-analyst/tabulate-area.htm)
