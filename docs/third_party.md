# 0.2.0b2 源码与第三方许可

ECalX 源码为 KihonnyTsang 自有 MIT 代码，沿用原公开仓库 LICENSE 版权和许可全文。BYO 开发源码输入的逐文件 SHA256 见 audit/source_provenance.json，完整 Git 历史保留。代码复用与适配不改变原版权声明。

examples 下三个原创 SYNTHETIC_TEST_DATA CSV 为 CC0-1.0 合成软件输入，不包含私人数据库/论文内容；测试中的临时数据同为合成。详见 examples/README.md。

第三方通过 pip 独立安装，不重打包到源码或项目 wheel。docs/dependency-licenses.json 来自本次隔离安装的发行元数据和实际许可文件哈希（仅相对路径），requirements-tested.txt 记录实测版本，不是所有平台通用锁文件。

直接依赖：Streamlit Apache-2.0；pandas BSD-3-Clause；NumPy BSD 及其 wheel 内多种许可；openpyxl/Pydantic/pytest MIT。传递依赖各自许可仍适用，包括 certifi MPL-2.0、typing_extensions PSF-2.0、packaging Apache-2.0 OR BSD-2-Clause 等。许可元数据不是把依赖重新许可为 MIT；没有缺省把未知许可判为 MIT。

源码与 wheel 文件范围审计不等于独立法律意见或所有者的新公开批准。用户自行导入数据/Key 的权利和隐私范围由其自己管理，不包含在发行中。
