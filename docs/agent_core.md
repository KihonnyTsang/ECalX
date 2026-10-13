# P3-2 Agent 核心公开评估

本评估依据原自有源码、P2 文件清单和 P3-0 所有者权属/MIT 确认。工程上可脱耦不等于独立法律或公开批准。没有复制 Key、认证缓存、账本、科研数据库、历史轨迹或原始数据。每个已选生产模块的源哈希和最终哈希见 audit/agent_source_provenance.json，最终全部文件另见 SHA256SUMS。

## 逐项结论

| 要求 | 纳入源码 | 脱耦方法/结论 |
| --- | --- | --- |
| SiliconFlow / DeepSeek Function Calling | siliconflow.py、config.py、llm.py | 保留真实 HTTP 编码、解码、预算和错误路径；新增显式付费确认和策略注入；不读原 .env，不自动选择 Provider |
| 多轮 tool_calls 与 role=tool | siliconflow.py、runtime.py | 保留原多调用队列、assistant 原消息、逐条 tool 反馈、缺失/重复 ID 拒绝；替身 HTTP 联测覆盖 |
| Runtime 与任务状态 | runtime.py、experiments/storage.py | 原持久化状态机、fcntl 租约、暂停取消、幂等/审计；构造函数改为显式 tools/storage_root；无默认实验/科研库构造 |
| Pydantic 与动态 Schema | contracts.py、capabilities.py | 原样保留；严格字段、状态能力列表；不注册审批/授权函数 |
| 服务端 Goals / PlanningProposal | planning.py、parsing/*.py、contracts.py | 原样保留；纯规则解析和宿主组装；模型输出不修补、不改 Goals |
| 权限、审批与预算执行 | runtime.py、api_policy.py、storage.py | 保留真实代码；去掉原项目固定授权哈希和旧风险例外 ID；可信宿主另行注入新授权摘要；发送前必须预留，UNKNOWN 挡住新任务 |
| 科研证据与收据来源链 | faithfulness.py、runtime.finish | 原样核验结构化来源、call ID/Pointer、值与版本；工具真实证据需未来独立授权接入 |
| 失败与最终报告门禁 | runtime.py、faithfulness.py | 原 LIVE_LLM FAILED 不可恢复/改写；finish 必须引用成功工具，校验失败拒绝报告；这是结构化报告门，不包含历史特定自然语言摘要自动验收链 |

## 发行适配的明确范围

- runtime.py 仅改 imports 和构造函数；实际 advance/finish/approve/save/control 等方法保留原实现。tools.py 是新增 Protocol 接口，明确没有科研结果实现；未把它称为生产 ScientificTools。
- llm.py 保留 SYSTEM、ProviderError、redact、Reply、LLMClient、原 ScriptedClient/DeterministicClient；排除 OpenAIResponsesClient，configured_client 改为仅显式 SiliconFlow 工厂。后两种离线客户端在源注释中明确不是在线 LLM 证明，也不会由 UI 自动构造。
- siliconflow.py 仅增加构造期显式付费确认与 ProjectAPIPolicy 注入；request_body、respond_budgeted、_decode 等传输/解码主体保留。
- api_policy.py 保留预算/事务/截止时间实现；authorization_sha256 由可信宿主显式传入，无原开发授权/旧风险例外；缺失时拒绝。CAPS 只是继承的软件上限，不是新所有者许可或当前供应商价格。
- api_reconciliation.py 仅收录从文件开始至 effective_usage 的契约、验证和保守账本 overlay；不收录后面的人工账单管理/恢复入口，防止误用原行政流程。无缺失的延迟 import。
- config.py 保留显式本地配置读取工具，但安装、UI、inspect、configured_client 都不调用它。仅在调用者主动调用 load_local_env(root) 时才读取给定根；该函数不是网络授权。
- capability/tool Schema 描述完整项目能力；发行版本身没有这些科研执行器。调用者必须注入符合 preview/execute 协议的工具，若需要 review_update 等完整实验功能还须提供 real 后端接口。

## 排除原因与可行的后续隔离

| 模块/工件 | 本次排除原因 | 评估结果 |
| --- | --- | --- |
| 原 agent/tools.py 的 ScientificTools / ReadOnlyEvidence | 同时依赖证据 Repository、候选生成、模型/实验服务、特定历史领域及数据库版本；源码自有不等于后端数据获准分发 | 源码未发现必须保密的凭据，可未来按工具拆分后依赖注入；本轮用明确 Protocol 隔离，不假造科研实现 |
| RealLaboratory / VirtualLaboratory、优化/ML 服务 | 扩展依赖、科研数据/模型/历史协议未在本轮逐件完成分发权利审核；不为展示而打包其数据 | 接口保留，实验执行未纳入；不是认定全部自有实验源码违法 |
| m4e41–m4e411、live_validation、summary_review 执行链 | 阶段专用任务、固定历史场景、出站投影和本地报告/数据库绑定；收录不完整会冒充完整线上验收 | 通用 Runtime 与报告门已保留；历史特定摘要门源码留在原项目，不伪造等价在线 PASSED |
| api_recovery、账单人工管理入口 | 原宿主行政恢复/旧 UNKNOWN 接受流程，不能继承到新发行版 | 仅保留预算执行及保守 overlay，后续须独立设计授权管理 |
| OpenAIResponsesClient / Codex app server | 超出本轮 DeepSeek 展示范围，存在额外在线执行与认证集成面 | 不增加未经同等授权/出站审核的第二在线 Provider |
| config/api_authorization、api_budget、.env、API 账本、认证缓存 | 私有授权/账务或秘密；原授权仅限原工程上下文 | 不复制；新授权摘要接口为空，显式确认不能代替独立政策文件 |
| 私有数据库、真实轨迹、研究结果、模型、来源 B/许可不明原始数据、论文、截图 | 未获逐件权利/隐私/科学表达批准，MIT 不重授权第三方内容 | 排除；不降低审核标准，也不用合成收据替换真实历史 |
| 原 P2 TEST_ONLY 包 | PENDING_INDEPENDENT_REVIEW，独立审核者与所有者批准为空 | 原地保留四文件及哈希，仅提供检查清单；UI B 为空 |

## 在线集成接口

此发行版没有可一键发请求的 CLI。未来有授权的宿主须分别提供：

1. 显式 provider='siliconflow' 与 confirmed_paid=True；有效服务端环境 Key、符合 config.py 固定允许列表的模型/官方 HTTPS 地址。允许列表是历史工程配置，不是当前服务可用性的保证；本轮未验证线上模型或价格。
2. 新独立目录下的 config/api_authorization.json（provider/model/endpoint/grant_id），以及宿主在审核最终字节后固定的 SHA256；不得从模型输出或不可信环境取得这个信任根。没有自动签署脚本或填好的授权文件。
3. config/api_budget.json，含 CAPS 内的限额、automatic_api_enabled 和由人核实且仍有效的价格/日期。不得把源码中的 CAPS 或测试价格当作许可/真实费率。需显式初始化独立空账本；不导入旧账本。
4. ProjectAPIPolicy(task_id, validator, root, authorization_sha256=...)；validator 必须对完整编码请求与当前宿主允许上下文做精确审核，不能用空回调放行；持久化预留在 HTTP 发送前完成。源中的 ROOT 默认仅指当前发行包位置，没有原工程路径；集成须显式指定新隔离目录。
5. ResearchAgent(root, storage_root=..., tools=...)，注入经权利/科学审核的真实工具；确认 Goals/Plan，并对状态工具的精确参数和预算独立审批。

这只是接口说明，没有替用户作新的付费授权，也没有构造线上任务。入口 A 始终不引用在线工厂。

## 验证范围

28 项核心测试实际调用生产来源的 Runtime、HTTP payload/_decode、真实预算策略和新的临时 SQLite。仅 HTTP opener 与科研工具结果是 TEST_ONLY 替身，socket/DNS 被禁用，SQLite 只能访问 pytest 临时目录。另有全源码 import 拒绝网络/建库、只读入口独立进程的导入隔离，以及 51 项 UI/展示门测试。

校验器验证精确结构与来源一致性，不验证论文真实性、物理结果或化学可行性；自由文本语义、权利与安全仍需人审。
