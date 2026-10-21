# BST电卡插值

这是基于单篇论文、四个已知BST组成的研究性插值工具，不是通用AI预测或新材料性能的实验验证。代码已包含在ECalX安装包内，不依赖作者电脑上的其他工程、模型或数据库，不调用付费API。

## 安装和使用

下载本发行候选的源码，解压后进入ECalX目录，使用Python 3.11或更高版本：

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install .
ecalx
```

打开终端显示的本机地址，在侧栏选择“BST电卡插值”。

1. 点击“选择参考CSV”，选择你合法取得的、与下述SHA256匹配的204点脱敏参考数据。文件只在本机用于此工具，不写入材料数据库。
2. Sr组成比例填写摩尔分数，例如 `0.20` 表示20%，不能填 `20`。
3. 温度使用K；标称电场使用kV/cm。例如填写 `0.20`、`323`、`30`。
4. 成功加载匹配参考数据后，页面显示“ΔT插值估计：约1.54 K”、ΔT–T曲线、参考组成、来源论文及图号。这个示例是已知曲线的组成插值，不代表新实验结果。

参考数据未随软件分发，也不会自动下载；没有匹配数据时，页面明确提示“尚未提供参考数据，无法计算”。合法取得数据的途径需由使用者与数据提供方自行确认；从论文重新读图可能产生不同数值，不能冒充冻结数据。任意CSV、PDF或其他版本均不会被接受。

[空白CSV模板](../ecalx_local/templates/BST_reference_template.csv)可直接填写，43列的用途见[字段说明](bst_csv_format.md)。模板不含数值，不是可计算参考集；任意填写不能代替合法取得且通过固定哈希校验的数据。

如果已合法持有原冻结CSV，可用源码附带脚本制作脱敏副本（不覆盖原文件）：

```sh
python scripts/prepare_bst_reference.py original.csv bst_reference.csv
```

脚本只删除私人路径列，逐项核对204条记录，并检查两个版本的固定哈希；它不授予数据再分发许可。持有脱敏副本的用户直接选取即可。也可在启动前显式指定文件：

```sh
export ECALX_BST_REFERENCE_CSV="/path/to/authorized/bst_reference.csv"
ecalx
```

该设置只指定数据文件，不加载外部计算代码；没有隐含目录搜索。界面选择的文件优先于此设置，错误上传不会回退到别的数据。

## 支持范围

- 材料为论文中的Ba₁₋ₓSrₓTiO₃块体陶瓷，x只支持0.05–0.35。参考x为0.05、0.15、0.25、0.35。
- 标称撤场幅值只支持10、20、30 kV/cm，不插值电场，不把负数自动转为正数。
- 温度使用对应参考曲线的实际覆盖交集；x=0.20、30 kV/cm时仅支持243.0–402.9 K。任何端点外输入都不计算。
- 已有组成命中记录时标“文献原始读图值”；记录之间标“温度插值”；相邻组成之间标“组成插值”。圆点是文献读图点，连线不是新增实验点。
- ΔT为文献正值降温幅值。数据为digitized读图数据，不是作者原始实验数组；完整加电波形未核实。页面不提供统计置信区间。
- 限制仅作用于BST工具，其他材料研究、导入和历史记录沿用原规则。

来源：Dai等，*Direct and indirect measurement of large electrocaloric effect in barium strontium titanate ceramics*，Int. J. Appl. Ceram. Technol. 17 (2020) 1354–1361，2019年在线发表，[DOI：10.1111/ijac.13384](https://doi.org/10.1111/ijac.13384)，图8A–D；使用的早期在线版PDF物理页为第6页。

## 数据版本与许可

原冻结204点CSV保持原字节不变；脱敏副本仅删除 `source_pdf_path` 列，并统一为UTF-8无BOM、LF换行，其余字段逐字符串一致，包括记录ID、组成、温度、电场、ΔT、DOI、图号和digitized标记。

| 版本 | SHA256 |
| --- | --- |
| 原冻结CSV | `b75cddb53985f24c8b825e3fbd732cb189e84570007b0bcfbc806a224d72badb` |
| 脱敏参考CSV | `92a17a6e4f10487d747c7e316883fbab88c1cdc03e6efdec0e11d4a18851ec89` |

数据副本目前仅供本地审阅和安装验收，未放入拟公开源码、源码压缩包或wheel。论文PDF、原图和全文同样排除。软件MIT许可不覆盖这些论文资产；具体核查和处理见[数据许可说明](bst_data_license.md)。

## 代码与检查

`ecalx_local/bst_interpolate.py` 包含原数值方法；只调整数据输入和完整性校验，移除绘图CLI及相邻目录加载。温度插值、组成权重、边界、曲线节点和来源输出逻辑保持不变。测试代码不附带真实论文数值数组；完整数值检查需显式提供合法参考文件：

```sh
ECALX_BST_TEST_CSV="/path/to/authorized/bst_reference.csv" python -B -m pytest tests/test_bst_ui.py -q
```

未提供数据时，普通测试仍检查无数据及错误数据的明确拒绝；需要参考曲线的检查标为跳过，不作为计算验收通过。
