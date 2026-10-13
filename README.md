# ECalX

面向开放科研需求的电卡材料研究 Agent：冻结 Goals、Function Calling、来源与逐候选收据、报告及分层验收。此发行候选保留生产来源的 Agent 核心代码，提供无 Key、无私有数据库的只读 Streamlit 展示。

**版本：0.1.0rc3 · 未签署审核基线：OWNER_REVIEW；公开批准以独立 `OWNER_APPROVAL.json` 的有效记录为准。** 本地演示使用最新五轮预写模拟，未运行真实 DeepSeek 或科研工具；chemical_pass 是模拟字段，两个组成未经验证、性能 unknown、M5 禁用。模拟不证明多轮自主决策、线上验收通过或材料发现。所有者审核不属于独立科学审核。

## 安装与验证（macOS / Python 3.11）

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-reproduction.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps '.[test]'
.venv/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check_imports.py
```

第三方依赖由 PyPI 安装，不随候选分发。根 MIT 只覆盖有权授权的自有内容；依赖及第三方科学资料不重新授权。安装可以联网；以下启动器关闭遥测、清除凭据变量、只监听 loopback 并拒绝非 loopback 出站和 DNS，不加载 .env。

## 待审预览（完整五轮演示，无需虚构批准）

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/run_readonly_offline.py --owner-review-preview --port 8535
```

打开 http://127.0.0.1:8535 。入口复制本地待审四文件到临时目录，显著标注未批准；显示五轮模拟、五条模拟收据、两个候选、来源关联及结构化报告。界面没有审批操作，不执行模型或科研工具，下载不代表公开授权。Ctrl-C 停止服务。

## 正式入口

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/run_readonly_offline.py --port 8536
```

A 模式无私有历史；B 模式在缺少真实所有者批准回执时保持 OWNER_REVIEW 空态。`--clean` 使用全新空工件目录，A/B 均为空。

## 最终审核与范围

- 最新 payload SHA256：`35bec3d5aac35463c3216ce6e613d6f4ae0539d51da61ef7ca1c844464088b0a`。
- `audit/test_only_review_manifest.json` 登记隔离修订四原件精确哈希；原 P2 哈希仅作历史，旧 review 不批准新版。
- `audit/release_whitelist.tsv` 是拟公开文件范围；`audit/SHA256SUMS` 覆盖其余所有发行文件。清单自身哈希及压缩包哈希由目录外最终报告提供，避免自引用。
- `demo/public/review.json` 始终保留为未签署 OWNER_REVIEW 审核基线，不表示是否已取得公开批准。实际批准由唯一的独立附件 `OWNER_APPROVAL.json` 决定；它须记录所有者真实确认及完整清单哈希和范围，使最终内容无需在确认后重新打包或改写。
- 所有文件变化使既有批准失效。回执是可追溯声明，不是受信数字签名，填入字符串不能证明真实所有者批准。不得让程序或测试替所有者签署。

[治理决定](docs/test_only_owner_governance.md) · [集成和批准契约](docs/clean_demo.md) · [安全检查](docs/release_check.md) · [真实核心说明](docs/agent_core.md) · [第三方许可](docs/third_party.md)

历史真实端到端 FAILED 与同一旧响应 POSTHOC 复评通过分别保留；本候选不包含真实运行数据、模型、账本、科研原件或原 Git。未发起新线上请求，不自动 Git 提交、推送、发布或部署。
