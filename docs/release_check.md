# 发布前核查

1. 所有者核验最新版四原件与最终文件白名单、逐文件 SHA256、清单自身 SHA256 及压缩包 SHA256。
2. 凭据、私人路径、预签名 URL、数据许可与第三方依赖许可检查通过；根 MIT 不覆盖第三方资料。
3. 完整发行测试、import 闭包、干净 venv 全部依赖安装、pip check、安装态代码测试及实际 Streamlit 检查完成。记录失败和限制，不沿用历史环境冒充新安装。
4. 正式 A 无私有记录，正式 B 在缺少有效独立 OWNER_APPROVAL.json 时为空；review.json 保留未签署 OWNER_REVIEW 基线。隔离预览显示最新版五轮预写模拟与全部科学限制；网络/数据库/执行模块边界保留。
5. 原 P2、隔离修订原件、真实 Runtime/数据/模型/账本/历史 FAILED 完整性核对不变。
6. 等所有者一次明确确认最终版本后才记录 OWNER_APPROVED；无明确 Git/发布指令继续停止。

扫描命令：python scripts/audit_release.py。源码目录不得包含 venv、build、egg-info、pyc、原 Git 或运行产物。正常开发安装产生的临时构建物须放目录外，保持最终白名单。
