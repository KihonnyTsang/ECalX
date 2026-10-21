# SYNTHETIC_TEST_DATA
本目录 CSV 为本次原创、CC0-1.0 的软件测试输入。化学式用于验证计量计算，温度与电场是人为设置的测试值；原示例和缺端点示例没有 ΔT/ΔS 标签。新增 delta_labels 示例含明确构造的软件测试 ΔT 标签；全部都不是科研测量或论文数据。
`example.invalid` 是不可用的占位来源，**不是文献**。每行保留 SYNTHETIC_TEST_DATA 标记；由此生成的候选也仅供软件测试，不构成科学证据。
可直接在导入页使用同名列自动映射。第三行的空温度/电场会保留 null。

新增 `SYNTHETIC_TEST_DATA_missing_endpoints.csv` 专门验证缺失端点：前四行端点为空，覆盖上限、ΔE、单独峰值以及 direct/indirect；第五行提供端点但协议文本未经核验。选择 ASSUMED 参考不能修改这些字段或把它们标记为 OBSERVED。

`SYNTHETIC_TEST_DATA_delta_labels.csv` 验证ΔE与原文标签的利用：缺端点记录可以幅度匹配；明确路径查询只能作为辅助证据。direct/indirect、温降幅值/Maxwell估计、正负号分别保留；上限和峰值不代填ΔE。

## BST参考CSV格式模板

另提供[空白BST参考模板](../ecalx_local/templates/BST_reference_template.csv)及[字段说明](../docs/bst_csv_format.md)。模板只有列名和一行空白，不包含任何论文数值，不属于可计算参考集；不能作为上文的合成材料记录导入示例。BST工具仍只接受合法取得、哈希匹配的冻结204点脱敏数据。
