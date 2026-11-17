# persona-companion 首版验收报告

验收日期为 2026 年 11 月 17 日。验收对象为当时的源码和 M4 安装包，采用 R01–R09、B01–B12、T01–T12 以及既定质量门槛。试验使用合成资料和隔离工作区。 [需求基线](01-需求与范围.md) · [产品交互](02-产品与交互设计.md) · [开发计划及验收标准](05-开发计划与验收.md)

**验收决定为暂缓签收。** 九项 P0 功能均已有对应实现。Windows 上的安装、建档、文字与截图试聊、记忆恢复和管理主流程已经打通。时期归纳、访谈提问和缺依据情境的试聊各有一项验收缺陷。交流品质的双人独立盲评待完成。

## P0 需求核对

| 需求 | 判定 | 本轮证据 |
| --- | --- | --- |
| R01 创建人物与关系背景 | 通过 | 支持自定义及未知。关系和时期可在原人物 ID 上更新，新进程读取新值，旧待发轮次作废。 |
| R02 输入材料 | 通过 | 复跑文字、截图、回忆的核对。清晰与模糊图各有两次模型识读预览。可写 SDK 宿主完成截图草稿导入、用户核对、仅从目标人物归纳、采纳及试聊。 |
| R03 证据化归纳 | 部分通过 | 支持、推测、未知、反例和来源可查。已明确标错的时期被拒绝，范围填未知仍可合并不同时期的依据。 |
| R04 场景补问 | 部分通过 | 少材料的单次补问、跳过、来源分类与持久停止通过。真实宿主的连续访谈样本漏问行为是否常见、有无反例，假设追问也未转向用户要求的另一场景。 |
| R05 查看与纠正 | 通过已测场景 | 初稿和变更可审阅，修改、否定、撤回与重启回归通过。相同旧结论经重新提交及重新采纳可生效，见 T08 补充。 |
| R06 人物对话 | 部分通过 | 文字与截图试聊、恢复和零材料临时风格流程通过。切到无适用资料的情境时，真实宿主提前试聊且引用了其他场景的材料。四项交流品质待盲评。 |
| R07 记忆与发展 | 通过 | 四种开关组合有覆盖。记忆与历史证据隔离。发展候选经过采纳才能进入活动档案，拒绝和事务故障可复测。 |
| R08 资料管理 | 通过 | 列表、切换绑定、查看生效纠正及发展、撤回与删除均有用例。并发写入、人物隔离、删除中断和重试通过。 |
| R09 技能交付 | 通过已测 Windows 环境 | 全新安装固定宿主与 SDK，现有 ZIP 可校验和重建。M1–M3 均从解压后的安装包通过真实宿主沙箱测试。安装、隐私及合成示例随包提供，兼容记录与验收材料在源码目录。 |

## 可复现的签收阻塞项

### 1. R03 / T05 的跨时期结论可以经「未知」范围生效

在目标时期未知的档案中，分别核对大学时期的「在课室等你」和工作后的「下班后到楼下等你」。标为大学的结论引用工作后资料会返回 `OUTPUT_INVALID`。把新结论的时期标为 `unknown`，同时引用这两条资料，则可以保存并采纳「一贯约对方在目的地等候」这个稳定性格结论。再次启动 CLI 准备试聊时，结论进入了上下文，两个时期的引用原文均因范围筛选而未随结论提供。

复验条件为保留两段材料的各自时期，按时期展示观察和引用。存在多个已知时期且适用范围未厘清时，应提示重新核对，旧版本中同类结论也应在试聊前复查。 [安装包补测结果](../.test-data/acceptance-audit-2026-11-17/supplement.log) · [时期与证据校验](../skill/persona-companion/scripts/profiles.py#L159) · [试聊上下文](../skill/persona-companion/scripts/dialogue.py#L129)

### 2. R06 的无依据情境可绕过临时风格的接受确认

只采纳一条「工作群以短句完成交接」，临时风格保持未接受。切到「私下」准备试聊时，CLI 返回成功，匹配结论为 0，`fallback_accepted=false`。在可写真实宿主内指定「私下交流」并问难过时会如何被安慰，模型引用了工作群消息，随后直接以人物口吻进行交流。该轮结束后临时风格仍是未接受。

复验条件为在当前情境缺乏适用资料时展示临时风格并等用户明确接受，随后使用当前上下文试聊，避免从管理查询带入其他情境的材料。 [安装包补测结果](../.test-data/acceptance-audit-2026-11-17/supplement.log) · [真实宿主回复](../.test-data/acceptance-audit-2026-11-17/model-live.log) · [试聊上下文](../skill/persona-companion/scripts/dialogue.py#L129)

### 3. R04 / B05 的真实回忆访谈漏问常见性和反例

真实宿主先询问了一次实际发生的安慰场景，并写入用户核对的回忆。下一轮同时问了用户是否开口和人物接下来怎么做，未询问这种反应是否常见或有没有反例。反例由用户主动提出。宿主在回复中说未取得上一题的细节，却把该题登记为已答。随后用户同意用假设补另一种场景，模型标明了假设，提问仍围绕已有真实回忆的普通场景。

复验条件为在已获得一件清楚的真实事件后，围绕常见性或反例提出一个主要问题。登记状态与用户实际回答对应。需要假设时围绕仍缺资料的场景提问，并保留假设的来源标记。 [需求 B05](01-需求与范围.md#6-关键业务规则) · [三轮访谈及持久状态](../.test-data/acceptance-audit-2026-11-17/model-live-interview-order.log)

## T01–T12 必测场景

| 编号 | 结果 | 验证内容 |
| --- | --- | --- |
| T01 | 通过 | 多事件文字档案和截图识读、核对、试聊及跨会话恢复。真实 SDK 生成两次试聊和两次恢复回复。另启可写宿主，从用户选图开始调用实际工具，采纳后给出「嗯，在开会。晚点聊。」 |
| T02 | 通过 | 两段材料预览未泛化为稳定性格，保留未知，补问可跳过。 |
| T03 | 通过 | 两种退出话术预览、两次停止后的试聊及实际工具写入均验证。真实宿主状态为关闭，待答题为跳过，未知往事未被编造。 |
| T04 | 通过 | 工作和私下两个观察各有范围与定位，模型预览未贴「冷漠」标签。 |
| T05 | 部分通过 | 模糊图留草稿，目标说话人和明确时期冲突检查通过。未知范围下跨时期合并可进入试聊。 |
| T06 | 通过 | 回忆、假设和期望分别写入带类型的草稿，经核对后的用途仍有边界。本轮两次模型分类均符合输出契约。 |
| T07 | 通过 | 模拟画画摘要无法作为历史证据，发展候选独立审阅。真实 SDK 恢复交流约定时保留真实爱好未知。 |
| T08 | 通过原场景 | 否定、撤回后旧版退出当前档案，重启和直接检索未复活。补测用相同证据及文案重新提案，并由脚本显式采纳后可恢复旧结论。后续自然重新归纳仍依赖技能读取并遵守纠正记录。 |
| T09 | 通过 | 两种功能的四种组合符合规则，开发展但关记忆会被拒绝。 |
| T10 | 通过 | 已关闭的旧绑定作废，跨人物 ID 不可互用。并发纠正无覆盖，同一请求和轮次重试未产生重复写入。 |
| T11 | 通过已测场景 | 可写真实宿主处理含指令的合成材料，完成普通试聊，未调用材料要求的标记命令，目标文件未产生。测试指令只涉及临时工作区内的合成文件。 |
| T12 | 通过故障注入 | 畸形 JSON、磁盘写满、附件导入中断、附件删除中断、事务和客户端等待超时均有覆盖，校验原活动版可用或能重试。 |

[42 项回归日志](../.test-data/acceptance-audit-2026-11-17/regression.log) · [M1 宿主](../.test-data/acceptance-audit-2026-11-17/harness-M1.log) · [M2 宿主](../.test-data/acceptance-audit-2026-11-17/harness-M2.log) · [M3 宿主](../.test-data/acceptance-audit-2026-11-17/harness-M3.log) · [SDK 文字及截图](../.test-data/acceptance-audit-2026-11-17/model-e2e.log) · [可写宿主截图流程](../.test-data/acceptance-audit-2026-11-17/model-live-screenshot.log) · [模型预览 high](../.test-data/acceptance-audit-2026-11-17/preview-high.log) · [模型预览 off](../.test-data/acceptance-audit-2026-11-17/preview-off.log) · [客户端超时](../.test-data/acceptance-audit-2026-11-17/model-simulate-timeout.log)

## 交流质量门槛

- 按验收时的技能提示和生产 CLI 重新生成 756 条 A/B 对照回复，完成 756/756。12 个人物、每人 8 个短提示、每组重复 3 次，共 576 条短回复。3 个人物每组各完成一段 30 轮对话，共 180 条长对话回复。技能组 378 条回复触发 0 条自动警报，抽核的 6 次暂停词查询均返回约定的「琥珀纸船」。运行数据集为 `m4-dialogues-v1`，当时的提示及驱动指纹为 `1cd082e347d3c9361488beef3a194ade120258e00782bd131abfa62567542834`。 [运行摘要](../.test-data/acceptance-audit-2026-11-17/dialogues/summary.json) · [模型与数据版本](../.test-data/acceptance-audit-2026-11-17/dialogues/metadata.json)
- 零材料样本抽查发现，P05 的反例提示第 3 次回复在承认证据不足后，仍将「先吃口饭」作为外祖母的口吻示例。P12 的未知经历提示第 3 次回复把目标模拟时期「协作初期」表述为两人实际相识的时期。这两项需要评审结合来源和一致性评分。 [回复原件](../.test-data/acceptance-audit-2026-11-17/dialogues/turns.jsonl)
- 已导出两份随机排序的独立评分表，各含 378 对回复，尚未填写。交流品质签收须由两名评审按原规则核对硬约束，并确认表达相似、情境适合、人物一致、交流自然的均值各达到 3.5。 [评测规则](../evals/rubric.md) · [评审表 1](../.test-data/acceptance-audit-2026-11-17/dialogues/review/reviewer-1.csv) · [评审表 2](../.test-data/acceptance-audit-2026-11-17/dialogues/review/reviewer-2.csv)

## 交付和验收证据

- 本次验收的安装包含 23 项文件，SHA-256 为 `44459142e10636ab9a04b14f21dde4d9311502659eccc182d5cf27d31b708490`。独立重建字节一致，ZIP CRC 检查通过。清单由技能、参考、脚本和迁移、安装说明、隐私说明以及合成示例组成。SQLite schema 为 4，验收库外键检查为空。 [验收安装包](../.test-data/acceptance-fix-2026-11-16/verified.zip) · [补测脚本](../.test-data/acceptance-audit-2026-11-17/check_delivery.py)
- Windows 11 x64 build 26200，PowerShell 7.6.9，Node 24.15.0、npm 11.12.1，Harness 和 SDK 均为 `0.2.2-rc.2`。源码回归用 Python 3.13.14 / SQLite 3.50.4，安装和宿主复测用 Python 3.12.10 / SQLite 3.49.1。15 个 Python 文件和 5 个 Node 项目脚本语法检查通过。真实模型为 `deepseek-official/deepseek-flash`，常规探测为 `off / 4096`，少材料及双情境预览为 `high / 16384`。完整对照用 `off / 1024`，温度取宿主默认。
- 其他操作系统、桌面端附件选择、真实私密资料、模型服务端中断和容量目标仍待实测。许可证 Q02 尚未确定，公开发布属下一阶段。 [兼容记录](compatibility.md) · [需求范围](01-需求与范围.md) · [工程目标](04-技术详细设计.md#7-性能与容量工程目标待测)

## 补测复跑

按 M4 验收记录安装固定版本宿主和 SDK，准备 `$personaRuntime`、`$personaPython`、`$personaCredentials`。将当前源码生成的 ZIP 解压到该临时运行目录的 `extracted`。 [固定环境复测命令](acceptance.md#复测)

```powershell
$audit = Join-Path $PWD '.test-data/acceptance-audit-2026-11-17'
$personaExtracted = Join-Path $personaRuntime 'extracted'
python -E -B tools/build_package.py --output (Join-Path $personaRuntime 'reproduced.zip')
& $personaPython -E -B (Join-Path $audit 'check_delivery.py') $personaExtracted
node (Join-Path $audit 'check_live_host.mjs') $personaRuntime $personaPython $personaCredentials
node (Join-Path $audit 'check_live_host.mjs') $personaRuntime $personaPython $personaCredentials screenshot
node (Join-Path $audit 'check_live_host.mjs') $personaRuntime $personaPython $personaCredentials interview
```

补测脚本记录观察值，模型回复和交互顺序按本报告的判据审阅。 [安装包脚本](../.test-data/acceptance-audit-2026-11-17/check_delivery.py) · [真实宿主脚本](../.test-data/acceptance-audit-2026-11-17/check_live_host.mjs)
