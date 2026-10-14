# ECalX 0.2.0b1 发布说明（候选）

计划 Git 标签：`v0.2.0-beta.1`。状态：本地发布分支，公开内容待所有者确认；未推送、未创建 GitHub Release、未部署。

从现有 main 初始提交 `efbb5598646be1b2e284660b103277915060c9a5` 延续 Git 历史，以最新 BYO 开发目录源码整合，不使用旧 ZIP。

- 独立依赖安装，默认 BYO 工作区；导入用户 CSV / Excel 与单位、空值、来源质量检查。
- 实际执行本地证据检索、组成候选生成、独立化学校验、报告导出和连续追问。
- 保留 SiliconFlow DeepSeek Function Calling 客户端、用户 Key 配置与估算预算；真实在线任务尚未独立验收。
- 整合 ΔE 通用幅度匹配、显式路径辅助证据、分口径描述统计、简化研究条件、ASSUMED 协议与新版 Streamlit UI。
- Python 元数据、包 `__version__` 与 UI 均为 `0.2.0b1`。化学规则/数据契约的 schema_version 保持原值，它们是协议标识而非项目版本。
- TEST_ONLY 合成示例只验证软件。BYO 没有可靠 ΔT/ΔS 正式预测，M5 禁用。

本次仅做发布整合与必要修复，无模型训练或付费 API 调用。旧演示入口、旧审计结果和旧批准文件从新版文件树退役，原提交全部保留；没有新的所有者批准。

macOS ARM64 / Python 3.11 的隔离安装与验收见 [validation.md](validation.md)。Linux / WSL2 未实测，原生 Windows 不支持。个人模式、实验框架和作者模型不属于独立 BYO 验收范围。
