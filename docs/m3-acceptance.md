# M3 对话、记忆与发展验收

2026-11-16。**M3 验收通过。** 验收使用合成人物、文字和截图，覆盖试聊、交流摘要、发展候选、采纳和重启恢复。

## 两条端到端示例

| 示例 | 从材料到恢复 |
| --- | --- |
| 文字 | 导入“你慢慢说，我先听你说完。”，核对目标说话人，采纳限于本次聊天的表达观察，试聊后保存暂停提示词。结束旧会话并启动新的模型会话，从私有库恢复提示词。模型说明真实人物的爱好缺乏依据。开启发展，展示并采纳“尊重暂停提示词”，核对历史结论保持原样。 |
| 截图 | 识读 [合成截图](../tests/fixtures/m2-clear.png) 中两条消息及其说话人。合成用户确认后才入档，采纳的结论仅引用目标说话人 `image:1:2`。在模拟开会情境中试聊，在新会话中恢复相应的暂停提示词，并对真实人物的爱好保留未知。 |

两个示例共用 [验收流程](../tests/m3_flow.mjs)。宿主沙箱探测通过真实 `skill`、`pwsh` 工具执行本地操作。SDK 探测使用真实模型识读截图并起草回复，由驱动模拟用户核对和采纳、调用生产 CLI 进行持久化，并在断言回复前复核轮次与版本。每次恢复使用新模型会话，只从当前本地库取档案和记忆。

## 环境与复测

| 项目 | 实测值 |
| --- | --- |
| 操作系统与运行时 | Windows 11 x64 build 26200，Node.js 24.15.0，npm 11.12.1，PowerShell 7.6.9 |
| 本地回归 | CPython 3.13.14（`fd17997`），SQLite 3.50.4 |
| 宿主和 SDK 探测使用的 Python | CPython 3.12.10（`0cc8128`），SQLite 3.49.1 |
| Harness 和 SDK | `@deepseek-ai/dsh@0.2.2-rc.2`，`@deepseek-ai/dsh-sdk-client@0.2.2-rc.2`。tag 和 commit 见 [兼容性验收记录](compatibility.md) |
| 真实模型 | `deepseek-official/deepseek-flash`，`reasoningEffort=off`，`maxTokens=4096`，温度沿用宿主默认 |
| 提示与数据 | 本阶段 [技能指令](../skill/persona-companion/SKILL.md)、[对话指南](../skill/persona-companion/references/dialogue-guide.md) 和合成流程。每次 SDK 执行包含 1 次截图识读、2 次试聊和 2 次恢复回复 |

在项目根目录运行。模型探测把合成数据交给当前已配置的 DeepSeek 官方模型服务。凭据可由已配置环境变量解析，也可用 `--credentials` 指向已存在的配置文件，直接由 Harness 读取。

```powershell
python -B -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q skill/persona-companion/scripts
node --check tests/m3_flow.mjs
node --check tests/m3_model_probe.mjs
node --check tests/m1_harness_probe.mjs

$m3Runtime = Join-Path ([IO.Path]::GetTempPath()) ('persona-m3-runtime-' + [guid]::NewGuid())
npm install --prefix $m3Runtime --registry https://registry.npmjs.org --no-audit --no-fund --save-exact @deepseek-ai/dsh@0.2.2-rc.2 @deepseek-ai/dsh-sdk-client@0.2.2-rc.2
$m3Python = Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'
pwsh -NoProfile -File tests/run_m1_harness.ps1 -Runtime $m3Runtime -Python $m3Python -Milestone M3
$m3Credentials = Join-Path $env:USERPROFILE '.dsh/.credentials.yaml'
node tests/m3_model_probe.mjs --runtime $m3Runtime --python $m3Python --credentials $m3Credentials
```

## 验收结果

| 层次 | 结果 |
| --- | --- |
| 本地回归 | 34/34 通过，M1 19 项、M2 6 项、M3 9 项。退出 0 |
| 静态检查 | Python 编译检查、三个 Node 验收脚本的语法检查均退出 0 |
| 真实宿主沙箱 | 文字及截图两条路径通过，退出 0。实际使用 `workspace-write` 从隔离 Git 项目加载技能，并经 `pwsh` 执行 Python。M1、M2 探测复跑同样退出 0 |
| 真实模型端到端 | 最终连续两次完整执行均退出 0。截图识读 2/2、试聊回复 4/4、恢复回复 4/4。10 次模型调用均观察到成功加载技能 |

本地覆盖了临时风格明确采纳、按情境和时期筛选、人物隔离、轮次凭证防篡改、记忆与发展开关四组合、记忆写入竞态、撤回及纠正后旧结论和摘要退出、删除与会话切换使旧轮次失效、发展拒绝去重、重试幂等、M2 档案原地迁移以及采纳事务故障后的回滚和重试。模拟爱好摘要可查看，作为历史证据提交时被 CLI 拒绝。

模型探测曾将“等我这边结束再找你”判为不满足稍后交流的断言。断言现识别这一等价表达，仍要求体现推迟交流。模型验证的范围是两个合成场景的可观察行为。

## 升级和回退

M3 首次打开旧库会在事务中把 SQLite `user_version` 升到 3，JSON `schema_version` 保持为 1。先停止使用该数据目录的脚本与会话，用 Python 的 `sqlite3.Connection.backup()` 保存独立的一致性备份，同时保留对应附件和原技能包。以备份副本试运行后再升级原数据目录。迁移失败会回滚。回退时暂停写入，成套恢复旧软件、配套数据库和附件，另存 M3 新增数据的备份。恢复前检查旧备份是否含后来已删除的人物。

实施决策 D：普通会话和摘要不推进人物修订号，以保持审阅期间的候选稳定。任一历史档案更新会将旧模拟摘要归档，并把已采纳的模拟发展保存在旧版本中，避免撤回或纠正的历史结论经模拟内容重新进入当前上下文。归档摘要可通过 `memory.list` 查看。

## 后续验收

桌面端附件交互、真实私人资料、两人盲评、30 轮长对话、跨平台安装和发行打包属于后续验收范围。模型复测只观察合成样本中的回复及来源边界。
