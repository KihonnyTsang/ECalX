# 第三方依赖与许可边界

P3-2 阶段在全新隔离 venv 从官方 PyPI 成功安装 Streamlit、Pydantic、pytest 及传递依赖；构建使用新环境的 setuptools。下表与 audit/dependency_licenses.json 是 P3-2 实际解析的闭包，不是 P3-1 旧环境记录。读取本地许可证元数据、可定位 LICENSE/COPYING/NOTICE/AUTHORS 正文并记录相对文件名、字节数与 SHA256。

当前闭包 44 个第三方分发包，未知许可证元数据为 0。没有将第三方源码、wheel、venv、前端资源或二进制纳入发行目录；它们各自的许可证不由 ECalX MIT 重新授权。

| 包 | 新环境版本 | 许可元数据 | 可定位许可/通知文件数 |
| --- | --- | --- | --- |
| altair | 6.3.0 | License text in package metadata (full locally read) | 1 |
| annotated-types | 0.8.0 | MIT | 1 |
| anyio | 4.15.1 | MIT | 1 |
| attrs | 26.1.0 | MIT | 1 |
| certifi | 2026.7.22 | MPL-2.0 | 1 |
| charset-normalizer | 3.5.2 | MIT | 1 |
| click | 8.5.0 | BSD-3-Clause | 1 |
| h11 | 0.16.0 | MIT | 1 |
| httptools | 0.9.0 | MIT | 3 |
| idna | 3.20 | BSD-3-Clause | 1 |
| iniconfig | 2.3.1 | MIT | 1 |
| itsdangerous | 2.2.0 | License :: OSI Approved :: BSD License | 1 |
| Jinja2 | 3.1.6 | License :: OSI Approved :: BSD License | 1 |
| jsonschema | 4.26.0 | MIT | 1 |
| jsonschema-specifications | 2025.9.1 | MIT | 1 |
| MarkupSafe | 3.0.4 | BSD-3-Clause | 1 |
| narwhals | 2.27.1 | MIT | 1 |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | 17 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | 3 |
| pandas | 3.0.6 | License text in package metadata (full locally read) | 1 |
| pillow | 12.3.0 | MIT-CMU | 1 |
| pluggy | 1.6.0 | MIT | 1 |
| protobuf | 7.36.2 | 3-Clause BSD License | 1 |
| pyarrow | 25.0.1 | Apache-2.0 | 2 |
| pydantic | 2.14.1 | MIT | 1 |
| pydantic_core | 2.50.1 | MIT | 1 |
| pydeck | 0.9.3 | Apache License 2.0 | 1 |
| Pygments | 2.21.0 | BSD-2-Clause | 2 |
| pytest | 8.4.2 | MIT | 2 |
| python-dateutil | 2.9.0.post0 | Dual License | 1 |
| python-multipart | 0.0.32 | Apache-2.0 | 1 |
| referencing | 0.37.0 | MIT | 1 |
| requests | 2.34.2 | Apache-2.0 | 2 |
| rpds-py | 2026.9.1 | MIT | 1 |
| setuptools | 84.0.0 | MIT | 15 |
| six | 1.17.0 | MIT | 1 |
| starlette | 1.7.0 | BSD-3-Clause | 1 |
| streamlit | 1.65.0 | Apache-2.0 | 0 |
| toml | 0.10.2 | MIT | 1 |
| typing-inspection | 0.4.4 | MIT | 1 |
| typing_extensions | 4.16.0 | PSF-2.0 | 1 |
| urllib3 | 2.8.0 | MIT | 1 |
| uvicorn | 0.54.0 | BSD-3-Clause | 1 |
| websockets | 17.2 | BSD-3-Clause | 1 |

Streamlit 安装包未提供可定位的许可正文；沿用 P3-1 对官方同版 1.65.0 Apache-2.0 LICENSE 的核对记录（P3-2/P3-3 均未再次联网读取）。本候选只声明依赖，不分发该安装包。其他只有元数据或复合许可的情况仍须按具体分发方式人审；元数据完整不等于全部二进制子组件、前端资源或专利权已审完。

requirements-reproduction.txt 固定本次观测版本，包含测试/构建闭包。日后可先安装该文件再安装项目；版本锁不保证公开仓库永远保留包，也不替代漏洞/法律审查。若以后分发 wheel/容器/安装包，应逐件核查版权、许可副本、NOTICE、传递二进制和平台差异；本轮不提供第三方打包物。

P3-3 本轮在已验证 P3-2 环境重新核对全部 44 个版本及 81 个可定位许可/通知文件 SHA256，均与记录一致；未发现额外源码第三方版权头/复制声明，不能据此排除未标注复制。自有 MIT 的版权/许可全文保留在根 LICENSE 和自有 wheel。Streamlit 缺本地许可正文的既有缺口明确保留。发行目录不打包依赖或其前端/二进制资源，因此不将这份源码候选当作完整第三方再分发包；未来若捆绑这些资源须重新履行各许可和 NOTICE 义务。P3-3 全新依赖下载未完成，不能把复用环境许可核对说成新安装成功。


## 本轮所有者审核候选的新环境核查
本轮全新 venv 从官方 PyPI 安装 requirements-reproduction.txt，include-system-site-packages=false；安装完成，pip check 通过。重新读取 44 个依赖及 81 个可定位许可/通知文件，未知许可元数据为 0，结果见 audit/dependency_licenses.json。Streamlit 本地许可正文缺口仍明确保留；本候选仅声明依赖，不分发第三方源码、wheel、前端资源或二进制。该缺口不被伪称为已补齐，也不改变将来捆绑分发时必须核查的义务。根 MIT 只授权有权授权的自有内容，最终范围和来源声明仍待所有者批准。
