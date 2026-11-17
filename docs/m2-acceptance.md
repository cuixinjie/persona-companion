# M2 人物构建验收记录

2026-11-16。**M2 人物构建验收通过。** 所有验收使用合成人物、文字和截图。M2 覆盖创建、证据核对和归纳、场景补问、档案采纳与纠正。

## 实测环境

| 项目 | 实测值 |
| --- | --- |
| 系统、宿主运行时 | Windows 11 x64 build 26200，Node.js 24.15.0，npm 11.12.1，PowerShell 7.6.9 |
| 本地回归 | CPython 3.13.14（`fd17997`），SQLite 3.50.4 |
| 宿主沙箱脚本 | CPython 3.12.10（`0cc8128`），SQLite 3.49.1 |
| Harness 和 SDK | `@deepseek-ai/dsh@0.2.2-rc.2` 与 `@deepseek-ai/dsh-sdk-client@0.2.2-rc.2`。固定 tag 与 commit 见 [兼容性记录](compatibility.md) |
| 真实模型 | DeepSeek 官方 `deepseek-official/deepseek-flash`，SDK profile。少材料与冲突使用 `reasoningEffort=high`、`maxTokens=16384`；其余使用 `off`、`4096`。温度沿用宿主默认，每例 2 个独立会话 |
| 指令与数据 | 本阶段 [技能指令](../skill/persona-companion/SKILL.md)、[M2 合成用例](../evals/cases.jsonl)、`tests/fixtures/m2-clear.png`（720×330）与其模糊版（360×160） |

## 复现

从项目根目录执行本地回归。

```powershell
python -B -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q skill/persona-companion/scripts
```

宿主验收使用一次性工作区和 `DSH_HOME`。安装固定版本，给 `$m2Python` 赋实际 Python 可执行文件的绝对路径。模型验收只将合成提示和截图发给当前配置的 DeepSeek 官方服务。它可从已配置的环境变量解析凭据，也可经明确指定的 `--credentials` 路径，由 Harness 直接读取既有凭据文件。

```powershell
$m2Runtime = Join-Path ([IO.Path]::GetTempPath()) ('persona-m2-runtime-' + [guid]::NewGuid())
npm install --prefix $m2Runtime --registry https://registry.npmjs.org --no-audit --no-fund --save-exact @deepseek-ai/dsh@0.2.2-rc.2 @deepseek-ai/dsh-sdk-client@0.2.2-rc.2
$m2Python = Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'
pwsh -NoProfile -File tests/run_m1_harness.ps1 -Runtime $m2Runtime -Python $m2Python -Milestone M2
$m2Credentials = Join-Path $env:USERPROFILE '.dsh/.credentials.yaml'
node tests/m2_model_probe.mjs --runtime $m2Runtime --credentials $m2Credentials --reasoning high --case M2-T02-sparse --case M2-T04-context
node tests/m2_model_probe.mjs --runtime $m2Runtime --credentials $m2Credentials --case M2-T03-skip --case M2-T05-clear --case M2-T05-blurred --case M2-T06-answer-kinds
```

## 验收结果

| 层次 | 实测结果 |
| --- | --- |
| 本地回归 | 25 例通过，含 M1 19 例、M2 6 例，退出 0。编译检查退出 0。覆盖少材料与临时风格确认、补问去重和跳过、三类回答的草稿与证据隔离、冲突分情境、纠正并发与幂等、重启后旧结论退出、M1 主库迁移与故障回滚 |
| 真实 Harness 沙箱 | M1 和 M2 探测均退出 0。M2 在 `workspace-write` 下实际加载 skill 并使用 `pwsh` 调用 CLI。清晰截图核对后按消息定位取证，未知说话人与另一方的内容无法用于归纳，模糊图保持草稿。访谈回答以回忆来源保存，逐项拒绝经采纳产生新版本 |
| 真实模型预览 | 6 个用例分组复测，每例 2 个独立会话。下表记录各用例最后一组结果，合计 12/12。每次均确认真实模型成功调用了 `skill` 工具 |

| 场景 | 检查 | 结果 |
| --- | --- | --- |
| T02 少材料 | 单事件只给可定位的观察，保留未知，仅提出一个补问 | 2/2 |
| T03 无记录且跳过 | 停止补问，保留未知，临时风格待用户接受 | 2/2 |
| T04 工作与私下 | 按情境保留各自结论与引用，未归纳为冷漠等人格标签 | 2/2 |
| T05 清晰截图 | 准确识读两条原文及两位说话人，等待用户核对 | 2/2 |
| T05 模糊截图 | 标记不可辨，未编造消息或形成候选结论 | 2/2 |
| T06 回答类型 | 按契约区分真实回忆、假设和用户风格期望 | 2/2 |

初次模型探测中，少材料用例达到过 4,096 token 上限。迭代复测观察到少材料时出现复合追问、停止后遗漏未知项、把场景对照写成额外性格条目，以及在截图核对前提出推测。相应规则已在技能指令中细化，复测按上述参数执行。模型表现仍可能随运行条件变化。

## 从 M1 升级和回退

M2 首次打开旧库会在事务中从 SQLite `user_version=1` 升到 2，JSON 请求的 `schema_version` 仍为 1。升级前关闭使用该 data-dir 的会话和脚本，用 Python 标准库 `sqlite3.Connection.backup()` 创建独立 SQLite 一致性备份，同时保留对应的 `attachments/` 和 M1 技能包。迁移异常会回滚数据库。若需退回 M1，暂停写入后用配套的旧库备份、附件与 M1 包恢复。M2 新增的变更需要留在独立的 M2 备份中。恢复旧备份后，先复查是否含备份之后已删除的人物，再按删除流程处理。

## 范围

模型预览和沙箱写入分别运行。模型预览核查识读、上下文和来源分类，沙箱验收核查人工确认后的档案持久化与纠正。桌面附件交互、真实私人材料、试聊、记忆和发展、长对话与双人盲评均留待后续阶段。
