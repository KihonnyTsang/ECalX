# 复现

安装、正式运行、隔离预览及 --clean 命令见根 README。使用 Python 3.11；本轮锁定 requirements-reproduction.txt 不升级业务依赖。

测试：PYTHONDONTWRITEBYTECODE=1 python -m pytest -q；import：PYTHONDONTWRITEBYTECODE=1 python scripts/check_imports.py；扫描：python scripts/audit_release.py。完整源码候选测试与安装态测试分别记录，重复用例不相加成为独立科学结果。

源码/模拟包展示不访问模型或科研工具，实际网络仅用于安装依赖。预览出站被拒绝，应用监听 127.0.0.1；无 Key、无 .env、无数据库/模型/reports。新环境安装及实际测试结果见 audit/validation.json；历史 P3-3 安装未完成记录仍在旧冻结候选，不改写。
