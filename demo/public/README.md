# TEST_ONLY · 五轮预写模拟演示（OWNER_REVIEW）

本包的 review.json 始终保留为未签署 OWNER_REVIEW 审核基线；是否取得公开批准由独立 OWNER_APPROVAL.json 的有效记录决定。第三方独立审核不再是 TEST_ONLY 发布的必要条件；所有者批准不属于独立科学审核。

五轮为预写模拟：search → generate → 两次 validate → finish → 最终摘要。没有运行真实 DeepSeek、网络请求或科研工具，不能证明多轮自主决策。chemical_pass=true 和 OK 均为模拟字段。两个 Sr/Ca/Ti/O 组成未经验证，结构未验证、性能 unknown、预测和实验值为空；M5 禁用，不声称新颖性或材料发现。

payload 来自隔离五轮修订原件，保持精确原字节。原作者声明为 Codex AI 辅助原创、未复制真实科研数据或第三方图片；这是待所有者核实的来源声明，不是自动许可认证。原件四文件及其旧 PENDING_INDEPENDENT_REVIEW 状态由 audit/test_only_review_manifest.json 登记；旧 review.json 没有用于批准本版本。

本地预览：按根 README 使用 --owner-review-preview。正式 B 仅在真实所有者回执绑定完整最终发行清单后显示内容。本地下载不代表公开授权。
