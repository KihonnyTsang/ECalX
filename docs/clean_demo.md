# TEST_ONLY 所有者审核与公开集成

最新版五轮 payload 按原字节保存在 demo/public；review.json 始终保留为未签署 OWNER_REVIEW 审核基线，是否取得公开批准由独立 OWNER_APPROVAL.json 的有效记录决定。正式 load_public 在无回执时返回空态。独立预览通过 --owner-review-preview 显示内容，不绕过正式 B 门禁。

## 一次最终确认，完整字节绑定

所有者审核最终压缩包、完整文件范围和 SHA256SUMS。确认后才可依据实际本人指令在解包根目录另附 OWNER_APPROVAL.json；不修改已冻结包内任何字节。压缩包作为不可变内容交付，回执作为独立授权附件。两者共同组成获准展示版本。

回执 JSON 契约：schema_version=2，status=OWNER_APPROVED，review_basis=OWNER_REVIEW，evidence_mode=TEST_ONLY，owner 与 review.json 所有者一致；owner_approval 为本人明确公开批准说明；reviewed_at 为实际时间；review_record 为可追溯的本人最终确认原文/依据；rights_review/privacy_review=APPROVED；payload_sha256 为最新版；release_manifest_sha256 为本人确认的 audit/SHA256SUMS 精确哈希；public_scope 列出清单全部路径及 audit/SHA256SUMS 自身。independent_reviewer 不填写，不称独立科学审核。当前不预生成通过回执，不提供自动批准脚本。

加载时验证未签署 review.json 的 schema、所有者、TEST_ONLY、payload 哈希、明确的演示文件范围；再验证真实批准回执、完整发行清单哈希、所有公开文件哈希和范围完全一致。缺项、旧版回执、哈希变化、额外公开文件、路径越界或符号链接均拒绝。本机安装/检出的 .git、.venv、__pycache__、.pytest_cache、build、egg-info 元数据不属于公开范围，运行时忽略；发行冻结扫描仍禁止把它们打包。OWNER_APPROVAL.json 是唯一不在内容清单中的授权附件，不能自我哈希。回执只是声明，不是数字签名；真实来源必须由所有者直接指令证实。

## 保留与限制

隔离修订四原件与 P2 原件原地保留 PENDING_INDEPENDENT_REVIEW，新 dossier 不继承旧批准。当前来源声明仍待所有者核实，许可/隐私检查不能由扫描自动批准。源码许可仅授权自有内容；所有第三方科研原行、图片、DOI/实验/预测值、真实模型身份、数据库、账本、历史轨迹均不纳入本演示。

五轮预写模拟及五条收据均未执行；chemical_pass 为模拟、两个组成未经验证、性能 unknown、结构未验证、M5=false。所有者可批准这些诚实标记的展示，不将其变为科学验证。

本轮不创建回执，不执行 Git 或公开动作。最终确认不自动授权付费 API 或部署。
