# GIS 可靠性修复：实现范围与验收

本次以本地源码 `eb39cb3` 为基线，参考用户提供的 reliability master spec 和 v1.0 配套包。配套包的审计基线不同，因此逐项复核实际代码，没有把包中的建议或示例标为已实现。

## 已实现

### 可验证的裁剪流程

新增三个 MCP 工具：

1. `arcgis_pro_analysis_asset_info(dataset_path, role, identity_basis, vector_layer="")`：登记物理文件、SHA256 数据集组成文件版本、完整 CRS WKT、网格，以及矢量要素/孔洞/部件数量。角色为 CONTINUOUS、CATEGORICAL、GRID_REFERENCE、REPORTING_AOI。`identity_basis` 是使用者声明的业务身份依据，不是服务器自动证明行政区正确。
2. `arcgis_pro_gp_clip_raster_checked(...)`：使用预先生成的 UUID；明确选择 POLYGON_MASK 或 RECTANGLE；显式提供最低覆盖率。矩形必须有坐标系 WKT；多边形与矩形不能同时提交。低于完整覆盖必须预先说明允许缺失的原因。
3. `arcgis_pro_analysis_result_status(run_id)`：重新核验输入和输出版本，返回执行状态、QA 状态、是否允许进入下一个 checked 步骤。客户端提交的元数据不能覆盖服务端重新读取的事实。

流程：冻结计划 → 校验输入版本和角色 → 校验 CRS/网格 → 创建输入快照 → 局部清除环境并执行 ArcPy Clip → 恢复环境 → GDAL 独立逐块核验 → 保存报告。ArcPy 与 GDAL 使用同一份已校验边界快照，但采用独立的裁剪和栅格化实现。

预期像元域在执行前由边界及独立参考网格确定。输出缩短到一半时，覆盖率分母仍是完整研究范围。多边形使用像元中心规则，保留孔洞和多部件；真实零值有效，NoData、GDAL mask 和非有限值被识别。检查全部块，不用 min/max 或抽样代替完整读取；生成 `missing_cells.tif`：0=应覆盖且有效，1=应覆盖但缺失，255=范围外。

输出位于 GP 输出根目录的 `_analysis_runs/<UUID>/`。合格运行得到 `qualified_asset.json`；QA 失败结果保留作诊断，不能被本 checked 流程继续使用。同一 UUID 同一计划仅返回已有状态，不重复写入；不同计划复用 UUID 被拒绝。输入或输出改变会使资格失效。已登记失败产物在同一输出根的运行记录内按主文件哈希识别，改名或移动不会自动获得资格。该限制不等于全局追踪所有文件：旧工具、换输出根、丢失运行记录或重新编码后的文件还未统一接入资产注册表。

### 已修复的现有入口

- 栅格计算器按 `arcpy.ia.RasterCalculator(rasters, input_names, expression)` 调用并显式保存返回栅格；移除 TypeError 后猜测另一套参数的重试。
- 计算器采用显式变量绑定与受限表达式。例如 expression=`x * 2 + 1`，input_rasters=`{"x":"<允许根内的绝对栅格路径>"}`。属性访问、索引、导入、任意函数和 Python 求值不可用。旧的引号图层名或 `Raster("path")` 表达式需要迁移到变量绑定；不静默猜测路径。
- 重分类增加 RANGE / VALUE、DATA / NODATA / ERROR。ERROR 在执行前逐块检查有效输入是否全部有映射，适用于此版本支持的 GeoTIFF；共享端点遵循 ArcGIS 的较低区间包含上端点语义。检查有限数、整数输出类别和区间重叠。ERROR 是本包装器策略，不是原生 ArcPy 枚举。
- clip / extract 新增局部 environment 参数；UNSET 不修改、CLEAR 清空、VALUE 明确赋值。为兼容旧调用，原先的 null/空字符串仍表示不设置。
- 分区统计显式暴露 DATA / NODATA 策略；尚未增加小分区有效样本数量诊断。
- 既有存在性验证返回 `verification_scope=EXISTENCE_ONLY`、`qa_status=NOT_CHECKED` 和 `eligible_for_downstream=false`；无 Exists 能力时不再返回已验证。历史 verified 字段仅保留存在性含义。旧工具的 `ok=true` 仍仅表示工具执行，不是空间质量认证。

## 当前限制与后续阶段

严格适配器限本地、单波段、北向上的实数 GeoTIFF（整数不超过 32 位，避免复数或 64 位整数转浮点后失真），以及本地多边形 Shapefile / GeoPackage；GeoJSON 只有 GDAL 与 ArcPy 均可读取时才允许。多波段、旋转网格、GUI 当前选择、定义查询、自动重投影不在本轮完整流程内，拒绝处理。合法自定义 CRS 不以 WKID 是否为 0 判定；但输入与网格必须经过 CRS 等价检查。此轮没有实现投影变换选择或统一所有旧工具的网格契约。

没有更改当前窗口的地图、工程、图层、数据、宿主会话和客户端配置。源码更新不等于已运行进程热更新。部署后须在方便时重新启动 MCP；如需 CURRENT，按既有流程停止旧宿主后重新接入，不能混合新旧模块。此处的 file-mode 工具不提供 CURRENT 降级。

这不是配套包全部 48 项验收已完成。向量联接/几何修复、网络可达性、距离上下游来源、机器学习防泄漏、全工具统一质量门禁、异步取消与资源调度仍为后续阶段。`scientific_suitability=NOT_ASSESSED`：计算和空间质量检查通过，仍不能证明论文模型及阈值适合研究对象。

## 验证方法

- 普通回归：`python -m unittest discover -s tests`；原生测试默认跳过。
- 静态检查：`python -m compileall -q arcgis_pro_mcp`、`ruff check .`。
- 原生验证：在 ArcGIS Pro Python 中设 `ARCGIS_MCP_RUN_NATIVE_QA=1`，运行 `tests/test_raster_checked_native.py`。测试只创建临时合成文件；可用 `ARCGIS_MCP_NATIVE_QA_REPORT` 指定私有结果文件。GDAL 与 NumPy 必须存在；不修改共享 Pro 环境。
- `tests/test_analysis_quality.py` 检查缺失门禁、WARNING/NOT_APPLICABLE、环境三态、整数/半像元平移，以及计算器调用与不重试。
- 原生用例覆盖 L 形 75、孔洞 84、分离多边形 8、矩形 100、条带缺失 90/100、缩短源文件 50/100、全 NoData、空像元中心域、半像元错位、继承环境清理与恢复、输入变更失效、改名诊断结果拦截，以及真实重分类和栅格代数。

这些是对应 T01/T02/T03/T04/T06/T07/T08/T10/T11/T12/T13/T15/T16/T17/T22/T33/T34/T35/T39/T47 的相关子情景；只有测试中的具体情景得到验证，不将整项所有条件或其他用例算作通过。后续复杂用例见用户提供的完整包。

## 原生接口依据

- [Esri Clip Raster](https://pro.arcgis.com/en/pro-app/3.6/tool-reference/data-management/clip.htm)
- [Esri Image Analyst RasterCalculator](https://pro.arcgis.com/en/pro-app/3.6/arcpy/image-analyst/raster-calculator.htm)
- [Esri Reclassify](https://pro.arcgis.com/en/pro-app/3.6/tool-reference/spatial-analyst/reclassify.htm)

## 本机验收结果（2026-09-09）

301 项普通测试断言通过，6 项真实 ArcPy 集成测试和 2 项 GDAL 元数据测试通过；ruff、compileall 和独立 stdio MCP 新工具目录检查通过。ArcGIS Pro 3.6。完整的分场景数值见 [验证记录](GIS_RELIABILITY_VALIDATION.json)。测试中 ArcGIS 报告了代码页 936 警告，但数值断言通过。极小、没有像元中心的范围由原生 Clip 拒绝，验证的是不能获得资格，不将其算作独立 QA 成功完成。
