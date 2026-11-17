# 验收记录（M4）

2026-11-16。M4 提供本地安装 ZIP、安装及隐私说明、合成示例和可复跑的行为评测。两位独立评审的盲评与质量门槛仍需签核。Q02 许可证待定，对外发布由 M5 在获得授权后执行。

## 甲方整改复验

本次复验使用的安装包含 23 个条目，SHA-256 为 `44459142e10636ab9a04b14f21dde4d9311502659eccc182d5cf27d31b708490`。以下复验使用合成资料，固定 Harness 与 SDK `0.2.2-rc.2`，模型为 `deepseek-official/deepseek-flash`。 [复验安装包](../.test-data/acceptance-fix-2026-11-16/verified.zip)

| 复验项 | 结果 |
| --- | --- |
| R01 关系与时期修改 | `persona.update` 保留原人物 ID。独立进程读到新值，旧提案和待发轮次失效，旧模拟摘要归档，原材料和结论范围保留。使用旧修订号的并发写入被拒绝，同一请求幂等重试。 |
| T03 停止访谈 | 无资料的两种结束措辞预览各两次，均保留未知并等待临时风格确认。CLI 核验状态跨进程保存、结束后拒绝改写追问，恢复访谈使旧轮次失效。真实 SDK 试聊两次保留未知。另在可写宿主直接发送原失败话术，模型加载技能并调用工具，随后读到访谈关闭、待答题跳过。回复未再追问历史。 |
| T05 证据时期校验 | 提案拒绝错期历史引用。旧错期提案不可采纳，旧活动错期结论和原文退出试聊。清晰和模糊截图预览各两次通过。 |
| 回归与迁移 | 42 项 Python 回归和安装 ZIP 的 M1–M3 真实宿主沙箱通过。真实 SDK 文字和截图端到端、跨会话恢复及客户端超时用例通过。首次端到端测试把「确定不了，只能先保留为未知」误判为未保留未知，补齐等价表述后完整复跑通过。从原 ZIP 建立 SQLite 3 合成资料并升级至 4，保留档案、旧轮次失效，外键检查无异常。 |

按下方复测命令使用**当前包**，补跑自然结束补问的用例。使用 `--interview-stop` 的 SDK 驱动负责读写及模拟用户确认，模型负责回复。

```powershell
node tests/m2_model_probe.mjs --runtime $personaRuntime --credentials $personaCredentials --case M2-T03-skip --case M2-T03-natural --case M2-T05-clear --case M2-T05-blurred
node tests/m3_model_probe.mjs --runtime $personaRuntime --python $personaPython --credentials $personaCredentials --extracted $personaExtracted --interview-stop
```

以下保留原 M4 包及原验收的历史记录。双人独立盲评仍待签核。

## 交付物与环境

- 原 M4 包含 22 个条目，SHA-256 为 `0956f973d7e3e2fc6b236df908c8ec640b0ece4ac14fc3a57f2c3bbd91eb9971`。同环境重建哈希一致。按清单检查了代码、迁移、参考文件、安装与隐私说明和固定的合成示例。包内没有数据库、真实材料、备份、凭据、缓存或测试输出。参见[安装说明](install.md)、[隐私说明](privacy.md)和[合成示例](synthetic-example.md)。
- Windows 11 x64 build 26200，Node.js 24.15.0，npm 11.12.1，PowerShell 7.6.9。源码回归用 Python 3.13.14 / SQLite 3.50.4，宿主流程和 SDK 用 Python 3.12.10 / SQLite 3.49.1。
- npm 宿主 `@deepseek-ai/dsh@0.2.2-rc.2` 与 SDK `@deepseek-ai/dsh-sdk-client@0.2.2-rc.2` 均全新安装在系统临时目录。宿主 tag 和 commit 见[兼容清单](compatibility.md)。
- 真实模型为 DeepSeek 官方 `deepseek-official/deepseek-flash`，温度为宿主默认。行为对照用 `reasoningEffort=off`、`maxTokens=1024`。端到端示例用 `off`、`4096`。构建预览的分组与参数按下方命令执行。

## 自动回归、宿主与故障验证

| 检查 | 实测结果 |
| --- | --- |
| Python 回归 | 39/39，退出 0。含 M1–M3 的 34 项和 M4 的 5 项 |
| 静态检查 | Python 源码编译和 Node 脚本语法检查退出 0 |
| 干净环境与真实沙箱 | 从 ZIP 解压至各自独立的临时 Git 项目。固定宿主通过真实 `skill` 和 `pwsh` 工具在 `workspace-write` 中运行 M1–M3，覆盖档案、撤回、删除、转录核对、文字及截图试聊和恢复 |
| 安装包 SDK 端到端 | 固定版 SDK 加载解压后的技能。1 次真实模型截图识读、2 次试聊、2 次新会话恢复均通过，退出 0 |
| 人物构建模型预览 | 最终 12/12 通过，两组选定参数均退出 0。少材料与两情境用 `high` / `16384`，其余用 `off` / `4096`，每例两次独立会话 |
| 模型等待超时 | 对真实宿主 SDK 的首轮回复强制施加 100 ms 客户端期限。驱动报告超时，验证原活动版和修订号未变、无新增记忆，退出 0 |
| 多事件档案 | 四次已核对事件形成档案。使用新 CLI 进程和新会话恢复同一版本，复习情境只取回相关的两条文字依据 |
| 附件写满与恢复 | 在 `fsync` 注入 ENOSPC，CLI 返回 `STORAGE_FAILED`。重启读到原活动版和原修订号。排除故障后以原请求重试，旧暂存清除，附件写入一次 |
| 迟到轮次 | 生产 CLI 创建短期有效凭证，时钟跨过有效期后由新进程调用 `chat.validate` 和 `memory.record`，两者均返回 `TURN_STALE`，原档案继续可用 |
| 删除中断与事务失败 | 复跑 M1/M3 的删除中断、附件导入中断、档案及发展采纳事务故障，验证重启、重试与版本恢复 |

## P0 核对

| 需求 | 已核对的行为 | 对应场景 |
| --- | --- | --- |
| R01 人物与关系 | 合成创建覆盖自定义关系和未知时期。少材料只保留已见场景。模型的人物表现见对照评测 | T02、T03 |
| R02 材料导入 | 文字及图像的目标说话人、定位与用户核对经过 CLI 和宿主检查。模糊图保留草稿。SDK 识读清晰图 | T01、T05 |
| R03 来源追踪 | 仅已核对目标人物证据支持活动历史结论。单事件不能支持稳定性格；试聊上下文分列来源、档案、模拟摘要与发展 | T02、T04、T06、T07 |
| R04 场景补问 | 跳过可持久化，不重复同一补问。三类回答分别记录，冲突按情境表达。模型预览的回合结果见下节 | T02–T06 |
| R05 纠正和撤回 | 旧提案无法覆盖新修订。拒绝或撤回后重启，旧结论及相应旧模拟上下文退出当前档案 | T08、T10 |
| R06 试聊 | 未采纳时阻止试聊，明确接受临时风格后可开始。SDK 用文字和截图生成回复，在新会话恢复合成约定并保留真实爱好未知。表达与交流品质待盲评 | T01、T03、T04 |
| R07 记忆与发展 | 四种开关组合、模拟内容写入边界、发展审阅、拒绝和竞态均有生产 CLI 回归 | T07、T09、T10 |
| R08 隔离和删除 | 不同人物不能互用 ID 或轮次凭证，切换和删除使旧绑定失效。并发纠正、重复请求、材料指令和磁盘故障均有回归 | T08、T10–T12 |
| R09 安装 | 从独立 ZIP 启动 CLI，迁移至 SQLite `user_version=3`。固定宿主完成包发现与端到端试聊 | T01、T05 |

## 模型评测和发布门槛

对比分为通用回复基线和使用生产 CLI 上下文的技能组。12 个人物各 8 个短提示，各提示两组各运行 3 次，共 576 条短回复。P01、P05、P11 在每组各进行一次 30 轮长对话，共 180 条回复。数据集和提示 SHA-256 随运行元数据保存。用同一模型、同一用户消息和同一推理参数，短提示使用全新会话。盲评按[评测规则](../evals/rubric.md)分别提供随机排序的 A/B 回复。

收紧指令前的完整运行收集了 756 条回复。技能组有两条在表明拒绝时逐字复述了同人物材料中的合成注入指令。该组未观察到工具调用或上传。已加强试聊指南，禁止在日常试聊回复中复述嵌入指令及其中的验证码。初次跑批还发现评测队列错误，修复后才得到完整数据。模型构建预览出现过异常 JSON、停止访谈后仍补问和场景观察越界。明确了停止规则和分类输出契约，并保留严格 JSON、来源及情境断言。

最终对照运行退出 0。756/756 条回复完成，技能组 378 条回复的自动检查未触发警报，长对话的 6 次约定查询均返回正确的暂停提示词。工程抽查发现，零材料的外祖母有一次用「我这老糊涂」回应未知经历。衣服颜色仍保留未知，句中的定型描述缺乏材料支持。运行元数据、合成回复及两份各 378 对的盲评表均保存在源码环境的 `.test-data/m4-dialogues-reviewed/`。

最终数据集 `m4-dialogues-v1` 的 SHA-256 为 `59c2cb01eaac7ee5b32483f6fe3305201b0653fb3af332b91f86c2f2dbf0d65d`。模型提示及驱动版本的组合 SHA-256 为 `59516b6cf5b72703cb575f805a7cd54a8cd3458441b83c86615afcede66f0930`。

双人独立盲评待完成。表达相似、情境适合、人物一致与交流自然四项均值尚未生成，暂不能判定各项是否达到 3.5。跨平台、桌面端附件交互、真实私人资料和模型服务端中断未测试。

## 复测

从源码项目根目录执行。所需模型凭据由 Harness 读取。以下命令使用合成资料。

```powershell
python -B -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
python -B tools/build_package.py
$personaRuntime = Join-Path ([IO.Path]::GetTempPath()) ('persona-m4-runtime-' + [guid]::NewGuid())
npm install --prefix $personaRuntime --registry https://registry.npmjs.org --no-audit --no-fund --save-exact @deepseek-ai/dsh@0.2.2-rc.2 @deepseek-ai/dsh-sdk-client@0.2.2-rc.2
$personaPython = Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'
$personaCredentials = Join-Path $env:USERPROFILE '.dsh/.credentials.yaml'
$personaZip = (Resolve-Path dist/persona-companion-m4.zip).Path
foreach ($milestone in ('M1', 'M2', 'M3')) {
    pwsh -NoProfile -File tests/run_m1_harness.ps1 -Runtime $personaRuntime -Python $personaPython -Milestone $milestone -Package $personaZip
    if ($LASTEXITCODE -ne 0) { throw "$milestone 宿主验收失败" }
}
$personaExtracted = Join-Path $personaRuntime 'extracted'
Expand-Archive -LiteralPath $personaZip -DestinationPath $personaExtracted
node tests/m3_model_probe.mjs --runtime $personaRuntime --python $personaPython --credentials $personaCredentials --extracted $personaExtracted
node tests/m3_model_probe.mjs --runtime $personaRuntime --python $personaPython --credentials $personaCredentials --extracted $personaExtracted --simulate-timeout
node tests/m2_model_probe.mjs --runtime $personaRuntime --credentials $personaCredentials --case M2-T02-sparse --case M2-T04-context --reasoning high
node tests/m2_model_probe.mjs --runtime $personaRuntime --credentials $personaCredentials --case M2-T03-skip --case M2-T05-clear --case M2-T05-blurred --case M2-T06-answer-kinds
```

行为对照和盲评命令见[评测规则](../evals/rubric.md)。
