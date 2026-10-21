# BST参考CSV：空白模板与字段说明

[下载或打开模板](../ecalx_local/templates/BST_reference_template.csv)。这是UTF-8、逗号分隔的普通CSV，包含43个列名和一行空白，可用表格软件或文本编辑器填写。列名保持英文原样，数值单元格不要加单位。

**模板没有论文数值，单独上传不能计算。** 当前BST工具仍只接受合法取得、SHA256匹配的冻结204点脱敏数据。自行填入其他数据、重新读图、改变格式或重新导出，均可能不符合固定字节哈希；模板不是接受任意数据的新接口。不要伪造缺失的来源信息。

如果已经合法取得匹配的脱敏参考文件，请直接上传该文件，不必用模板重新制作。持有原冻结CSV者可按[BST使用说明](bst_interpolation_20261021.md)制作脱敏副本。软件不提供数值下载链接，也不自动下载论文。

## 填写哪些字段

计算使用Sr比例、温度、标称撤场幅值、ΔT幅值，并核对记录ID、DOI、测量方法、来源类型和温变口径。曲线ID、样品标签、图号和PDF物理页用于回溯来源。其余列保留原参考数据的来源和读图审计信息；固定SHA256校验涵盖整个文件，不只校验计算列。

标为“可空”的字段保留原文未知状态，不补零。已冻结参考文件的原有内容仍须逐字保留。

| 列名 | 格式与含义 |
| --- | --- |
| `observation_id` | 文本；每个观测的唯一记录ID |
| `curve_id` | 文本；同一参考曲线共用的ID |
| `marker_index` | 整数；原图标记点序号 |
| `source_id` | 文本；来源论文标识 |
| `source_doi` | DOI；当前固定来源10.1111/ijac.13384 |
| `source_url` | HTTPS来源链接 |
| `source_pdf_sha256` | 来源PDF版本的SHA256，不附带PDF |
| `composition` | 参考组成的化学式文本 |
| `sr_molar_fraction` | 无量纲小数；Sr摩尔分数x，计算使用 |
| `sample_group_id` | 来源样品组标识 |
| `author_sample_label` | 论文中的样品标签 |
| `physical_specimen_id` | 实际样品身份，可空 |
| `preparation_batch_id` | 制备批次，可空 |
| `temperature_K` | 数值，K；读图温度，计算使用 |
| `temperature_axis_value_K` | 数值，K；坐标标定温度审计值 |
| `temperature_nominal_K` | 数值，K；原文明确报告的名义温度，可空 |
| `field_kV_cm` | 数值，kV/cm；文献标称撤场幅值，计算使用 |
| `field_initial_nominal_kV_cm` | 数值，kV/cm；文献名义起始场，不代表已核实波形 |
| `field_final_nominal_kV_cm` | 数值，kV/cm；文献名义终止场，不代表已核实波形 |
| `field_endpoint_evidence` | 名义场端点的原文依据与核实状态 |
| `delta_T_magnitude_K` | 数值，K；正值降温幅值，计算使用 |
| `delta_T_axis_value_K` | 数值，K；坐标标定ΔT审计值 |
| `delta_T_signed_K` | 数值，K；带符号温变，可空，不由幅值自动补出 |
| `target_definition` | 当前为`published_positive_cooling_magnitude_after_field_removal` |
| `method` | 当前为`direct_thermocouple` |
| `data_origin` | 当前为`digitized`；不标成原始实验数组 |
| `published_processing` | 论文已报告的数据处理说明 |
| `figure` | 图号：8A、8B、8C或8D |
| `pdf_page` | 使用的PDF物理页整数，从1计数 |
| `local_printed_page` | 本地版本的印刷页标识 |
| `final_issue_page_mapping` | 最终卷期页码映射及核实状态 |
| `protocol_pdf_pages` | 协议涉及的PDF页码，多值用分号 |
| `source_image` | 原图相对标识；只作元数据，不读取或分发图片，不填私人绝对路径 |
| `calibration_id` | 坐标标定标识 |
| `pixel_x` | 数值，像素；读图点横坐标审计值 |
| `pixel_y` | 数值，像素；读图点纵坐标审计值 |
| `digitization_sensitivity_T_K` | 数值，K；读图敏感度，不是实验不确定度 |
| `digitization_sensitivity_delta_T_K` | 数值，K；ΔT读图敏感度，不是统计置信区间 |
| `sensitivity_type` | 上述敏感度的定义与限制 |
| `plotted_error_bars` | 原图误差线说明，不转换为结果的置信区间 |
| `independent_replicate_count` | 独立重复次数整数，未知留空 |
| `raw_array_available` | 布尔文本；原始数组可得性，按来源如实记录 |
| `protocol_id` | 来源实验协议标识 |

## 支持条件和提示

只覆盖Dai系列Ba₁₋ₓSrₓTiO₃块体陶瓷，参考x为0.05、0.15、0.25、0.35，标称撤场幅值为10、20、30 kV/cm。冻结数据共12条曲线，每条17个读图点，合计204条。查询温度按所需曲线的实际交集判断，不外推。

- 未提供参考文件：提示“尚未提供参考数据，无法计算”，不显示任何ΔT。
- 空模板、其他自填数据、版本不符或文件无法读取：校验失败，不计算。
- 文件有效但查询条件超范围：明确拒绝，不保留旧结果或曲线。

软件MIT许可不覆盖论文及数字化数值；参考数据需由用户自行合法取得。模板不含这些数值，也不授予数据再分发许可。

**English:** Fillable format template only, not reference measurements. All 43 columns are documented above. Calculation still requires the authorized, checksum-matching frozen reference CSV. An empty or independently filled template is rejected.
