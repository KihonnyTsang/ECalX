# ECalX · BYO API & Data

**v0.2.0-beta.2 发布候选**（Python版本号 `0.2.0b2`）。这是基于现有Git历史准备的本地候选，尚未推送或创建Release。无需作者电脑上的工程、模型或私人数据库。

ECalX是本地材料研究工作台。“BYO”是Bring Your Own，即使用你自己的API和数据：

| 用途 | 需要准备什么 | 能做什么 |
| --- | --- | --- |
| BYO API · 自备API | 可选：你自己的SiliconFlow Key和可用DeepSeek模型 | 在线研究与追问；不填Key也可使用本地功能 |
| BYO Data · 自备材料数据 | 你自己的CSV、Excel，或本地可搜索PDF | 导入资料、检索证据、探索组成候选、查看报告 |
| BST研究性插值 | 合法取得且哈希匹配的204点脱敏参考CSV | 在已有BST曲线覆盖内估计ΔT；不能替代实验验证 |

## 安装与启动

使用Python 3.11或更高版本。下载本候选源码并解压，进入ECalX目录后执行：

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install .
ecalx
```

打开终端显示的本机地址。默认只监听127.0.0.1，不收集Streamlit使用统计。源码也可运行：

```sh
python -m streamlit run app.py --server.address=127.0.0.1 --browser.gatherUsageStats=false
```

仅实测macOS ARM64/Python 3.11；其他平台未实测。完整依赖由pip安装，不需要训练模型或CUDA。源码和wheel使用相同的包内BST计算代码。

## BYO Data：导入你自己的材料资料

进入“设置→数据库构建”，导入CSV或Excel并映射字段，或本地解析PDF后核对保存。CSV/Excel至少要有材料化学式，已有温度、电场、ΔT等数值需注明单位；未知条件保持空白。可用[合成软件示例](examples/README.md)熟悉操作，示例不是科研证据。

在“研究工作台”填写问题、检查条件，运行证据检索、组成候选生成和独立化学校验，再查看报告或追问。新候选的正式性能保持unknown，M5正式推荐未启用。BST工具的材料和条件范围不限制这些普通研究功能。

资料、历史和报告默认保存到 `~/.local/share/ecalx`。可在启动前指定 `ECALX_WORKSPACE`。

## BYO API：可选的在线研究

进入“设置→API配置”，填写自己的SiliconFlow API Key和账户可用、支持Function Calling的DeepSeek模型。在线请求会由供应商收费；开始前确认用量和发送范围。没有Key时仍可导入数据和运行本地研究。

Key及配置保存在 `~/.config/ecalx`，可用 `ECALX_CONFIG_DIR` 指定目录；不要将Key写入研究问题或CSV。原始文件、完整数据库和模型文件不向供应商发送。已有请求/Token限制继续生效；真实BYO供应商在线验收尚未完成，本轮没有调用付费API。

## BST电卡插值：先提供参考数据，再计算

进入侧栏“BST电卡插值”，点击“选择参考CSV”。**未提供匹配的参考数据时，BST插值不可计算，页面不会显示1.54 K或任何替代数值。** 软件不附带、也不会自动下载原204条数据、论文PDF或原图，因为当前未确认这些数字化数据的公开再分发许可。

提供[空白CSV模板](ecalx_local/templates/BST_reference_template.csv)和[字段填写说明](docs/bst_csv_format.md)。模板可用表格软件或文本编辑器填写，但不含参考数值，也不是可计算数据集。当前功能仍只接受合法取得、SHA256匹配的冻结204点脱敏CSV；任意自行填写或重新读图的数据不会自动通过校验。普通BYO材料数据不受这个冻结版本要求限制。

取得匹配参考数据后，填写Sr摩尔分数x（`0.20`表示20%，不是`20`）、温度K和文献标称撤场幅值kV/cm。支持范围为Ba₁₋ₓSrₓTiO₃块体陶瓷，x=0.05–0.35，场仅10、20、30 kV/cm，温度按所需参考曲线的实际交集判断；超范围不计算。

页面区分“文献原始读图值”“温度插值”“组成插值”，显示参考组成、权重、DOI与图号。只在成功加载匹配数据后，x=0.20、323 K、30 kV/cm的示例才会计算并显示“ΔT插值估计：约1.54 K”。这来自已知曲线的研究性插值，不是通用AI预测或新材料性能的实验验证。

来源：Dai等，[DOI：10.1111/ijac.13384](https://doi.org/10.1111/ijac.13384)，图8A–D，digitized读图数据。完整使用方法、数据哈希及许可处理见[BST说明](docs/bst_interpolation_20261021.md)和[许可核查](docs/bst_data_license.md)。也可通过 `ECALX_BST_REFERENCE_CSV` 显式指定本机合法参考文件。

## 验证与许可

```sh
python -m pip install '.[dev]'
python -B -m pytest -q
python scripts/audit_release.py
```

已有独立候选的182项通过记录原样保留，本候选的版本、安装和测试结果见[验证说明](docs/validation.md)。无合法BST数据时，相关数值检查会明确跳过，不能把跳过当作计算成功。发布文件和哈希见 `audit/public_files.txt`、`audit/SHA256SUMS`。

源码遵循[MIT许可](LICENSE)，第三方依赖保留各自许可。MIT不覆盖论文、用户数据或未获再分发许可的数字化数据；后续如确认相关权利，再单独决定是否公开数值数据。

**English:** Bring your own API key and materials data. Local research works without an API key. BST is research literature interpolation, not a general AI performance predictor. Reference measurements are not bundled: provide a legally obtained, checksum-matching reference CSV. The blank CSV template documents the format; it cannot produce a calculation by itself.
