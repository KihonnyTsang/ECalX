# ECalX · Bring Your Own API & Data

本地材料研究工作台，Python / UI 版本 **`0.2.0b1`**，拟用 Git 标签 **`v0.2.0-beta.1`**。
这是待所有者确认公开内容的预发布候选；本地准备不表示已经推送或发布。

第三方可独立安装、导入自己的 CSV/Excel，运行真实本地科研工具，并配置自己的 SiliconFlow API Key。默认 BYO 模式不寻找或依赖作者的原科研目录、私人数据库、模型或账本。

| 能力 / 内容 | 当前状态 |
| --- | --- |
| CSV / `.xlsx` 导入、证据检索、组成候选生成、独立化学校验、报告与连续追问 | 已验证：真实本地 Python 工具执行，合成数据用于软件验收 |
| BYO DeepSeek Function Calling 在线研究 / 在线追问 | 客户端和模拟 HTTP 契约已测试；**真实在线任务尚未独立验收** |
| TEST_ONLY / SYNTHETIC_TEST_DATA 示例 | 原创合成软件测试数据，**不是论文、测量或科研证据** |
| 新候选 ΔT / ΔS 正式预测、M5 正式推荐 | **无可靠正式预测，数值为 unknown；M5 禁用** |

## 独立下载与安装

需要 Python 3.11+；本次实际验证 macOS ARM64 / Python 3.11。Linux / WSL2 尚未实测；原生 Windows 不支持（使用 POSIX 文件权限和进程机制）。无需 CUDA、训练模型或私人数据。更高 Python 版本尚未逐一验证。

发布后从本仓库的 `v0.2.0-beta.1` 标签下载并解压源码，进入解压目录；或在标签发布后执行：

```bash
git clone --branch v0.2.0-beta.1 https://github.com/KihonnyTsang/ECalX.git
cd ECalX
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install .
ecalx
```

当前公开 main 仍是旧版，标签需另行确认后才会发布。安装测试与冻结依赖见 [验证说明](docs/validation.md)。

`ecalx` 打开已安装包内的 Streamlit 应用。浏览器访问终端给出的 `http://127.0.0.1:8501`；默认仅监听本机并禁用 Streamlit 使用统计。源码安装也可运行：

```bash
python -m streamlit run app.py --server.address=127.0.0.1 --browser.gatherUsageStats=false
```

## 无 Key 的离线使用

1. 从侧栏 **我的材料数据** 上传 UTF-8 CSV 或 Excel `.xlsx`，选择工作表并映射字段。旧 `.xls` 请另存为 `.xlsx`。
2. 至少映射材料化学式。含数值的字段必须有对应单位列或明确的统一单位；检查预览、换算、空值及错误。存在错误时整批不写入，确认后保存原文件字节、映射与导入版本。
3. 点击 **新建研究**，输入问题及可选条件，检查并确认生效条件，选择本地科研工具。确认计划后继续执行。
4. 查看候选、来源、条件和真实工具收据，下载 Markdown 报告、任务 JSON；继续追问、比较候选。修改条件会建立新任务草稿，旧任务保持不变。

没有 Key 时在线入口禁用，离线流程仍完整可用，不向模型供应商发请求。离线追问以已保存证据和规则回答，没有 LLM 自由推理。

可导入 [合成测试 CSV](examples/SYNTHETIC_TEST_DATA.csv) 熟悉操作。`example.invalid` 是占位来源，不是文献；所有示例数值均为软件测试构造，其中 `delta_labels` 示例有合成 ΔT 标签。由示例产生的候选和报告仅用于软件验证。

## 自己的 Key、模型与预算

在 **首次配置** 填写自己的 SiliconFlow Key、账户可用且支持 Function Calling 的 DeepSeek 模型 ID、供应商当前输入/输出单价和每日本地估算预算。支持 `deepseek-ai/DeepSeek-*` 格式；界面示例不是模型可用性或供应商当前定价的保证。配置检查不发请求。

Key 保存到 `~/.config/ecalx/.env`，目录权限 700、文件权限 600；模型和预算保存在同目录 `settings.json`。`ECALX_CONFIG_DIR` 可指定自己的受保护目录；进程环境变量 `SILICONFLOW_API_KEY` 优先。不要在研究问题、来源字段或追问中填写密钥。

在线研究要求确认冻结条件、当次预算、外发范围及 Agent 计划。每次请求核对配置和预算；配置变更后需重新确认。每任务最多 10 次请求、16 次工具调用、累计 131072 Token，自动重试 0 次。失败任务不自动重发。

外发内容：研究问题、冻结条件、工具定义；最多 8 条记录的组成/温度/场路径/方法/来源摘要，最多 4 个候选及少量种子来源/化学检查。追问仅外发所选证据块和最近 6 轮问题。原始上传文件、数据库原件、raw_metadata、工艺自由文本和模型文件不外发。供应商使用固定 HTTPS API，拒绝重定向。

本机工作区 `api_usage.sqlite3` 记录用户自己的调用预留与用量。未知用量保留预留并阻止自动继续，需用户在供应商账单核对。预算按用户填写单价估算，**不是供应商账单硬限额**；没有自动对账/清零功能。本次发布验证未调用付费 API，真实 BYO 在线任务仍待独立验收。

## 数据与科学边界

支持化学式、组成说明、温度、电场起终点/ΔE/上限/独立峰值、加载方向、direct/indirect、DOI/URL、样品形态/身份、协议、已有 ΔT/ΔS 标签及物理量口径/符号/单位、其他性能名称/值/单位。字段详见导入页。

- 空值保存为 null，不补温度或场端点为 0。ΔE、上限和峰值互不代替；已填端点与 ΔE 矛盾会报错。
- 温度转换为 K、电场为 kV/cm；原值与原单位保留。ΔS 明确单位后换算为 J/(kg K)，ΔT 的 K/°C 温差不加 273.15。
- ΔE 查询无需完整端点，支持 10/20/30/40/50 和非整数。明确 `0→20` 等路径时，缺端点的 ΔE 记录单列“幅度匹配、路径未确认”辅助证据，不计严格路径匹配。
- 描述统计按来源、组成、温度、ΔE、direct/indirect、物理量口径、符号和协议分组；不改变标签符号，不生成跨协议性能排行榜。已有标签不等于候选预测。
- 研究条件以温度、ΔE 和排除元素为常用输入，高级条件可填上限或路径。常用脉冲 `0→峰值→保持→0` 标为 ASSUMED；保持时间/升降速率/测量分支未知时不补齐。导入页的假设开关不写回真实端点。用户上传数据或 OBSERVED 自由文本不构成独立核验。
- 来源缺失记录可检索，不作生成种子；用户提供 DOI/URL 不等于论文已独立核验。候选生成实际运行计量、位点替代/电荷补偿规则，独立校验化学约束，目前覆盖 ABO3、AO2、AB2O4。证据不足或超出规则可返回空结果。
- 新候选始终 `generated_unverified`，ΔT/ΔS 为 `unknown`。BYO 不加载作者 RF/XGBoost，不训练模型；导入标签也不会开启预测。M5 禁用。

[ΔE 规则](docs/delta_evidence_matching_20261014.md) · [协议规则](docs/field_protocol_20261014.md)

默认工作区 `~/.local/share/ecalx` 保存原始数据、导入版本、数据库、冻结快照、任务和对话。可在启动前指定：

```bash
export ECALX_MODE=byo
export ECALX_WORKSPACE="$PWD/my-local-workspace"
export ECALX_CONFIG_DIR="$PWD/my-private-config"
ecalx
```

每个研究冻结数据快照，后续导入不会改写历史报告/追问。配置和工作区是私人运行数据，不应提交到 Git。

## 支持范围与未支持功能

公开 BYO 仅开放检索、候选生成、化学校验、研究报告四个科研工具。无真实论文联网搜索/自动下载、DFT、模型加载/训练、实验设备控制、M5 正式推荐、插件市场或云服务。源码内保留原实验框架和显式 personal 兼容接口，它们不表示独立安装拥有实验后端或预测能力。

`ECALX_MODE=personal` 仅供已经拥有原完整工程的人显式设置 `ECALX_SCIENCE_ROOT` 和 `ECALX_LOCAL_STORAGE` 使用；需要额外数据/模型/依赖，不包含在本次公开发行和独立 BYO 验收范围内。默认 BYO 不读取这些资产。

## 测试、许可与发布审阅

```bash
python -m pip install ".[dev]" -c requirements-tested.txt
python -m pytest -q
python scripts/audit_release.py
```

HTTP 契约测试用模拟供应商响应实际验证编码/解码、预算和任务状态机；**不是供应商联网或在线推理验收**。

源码 MIT，保留原版权；原创测试 CSV 为 CC0-1.0。依赖遵循各自许可，源码发行不重打包第三方库。见 [第三方许可](docs/third_party.md)、[公开范围](docs/release_scope.md)、[发布说明](docs/release_notes.md) 和 `audit/public_files.txt` / `audit/SHA256SUMS`。

旧 `OWNER_APPROVAL.json` 只适用于 `0.1.0rc3-final1`，仍保留在原提交历史，已从新版文件树移除。它不批准 `0.2.0b1`；本候选没有伪造新批准，待所有者确认最终公开内容后再推送和创建 Pre-release。
