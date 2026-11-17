# M1 数据基础验收记录

2026-11-15。**M1 验收通过。** 所有操作使用隔离目录中的合成资料。

## 实测环境

| 项目 | 实测值 |
| --- | --- |
| 系统 | Windows 11 x64，build 26200 |
| 本地回归 | CPython 3.13.14（`fd17997`），SQLite 3.50.4 |
| 宿主沙箱内脚本 | CPython 3.12.10（`0cc8128`），SQLite 3.49.1 |
| Harness | npm `@deepseek-ai/dsh@0.2.2-rc.2`，headless profile，`workspace-write`；固定 tag 和 commit 见 [兼容性记录](compatibility.md) |
| 宿主运行时 | Node.js 24.15.0，npm 11.12.1，PowerShell 7.6.9 |
| 模型 | 本阶段宿主探测直接调度真实工具，没有模型请求。M0 的官方 `deepseek-flash` 服务、参数和合成截图识读结果见 [兼容性记录](compatibility.md) |

## 可复现命令

从项目根目录执行。

```powershell
python -B -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q skill/persona-companion/scripts
$m1Data = Join-Path ([IO.Path]::GetTempPath()) ('persona-m1-example-' + [guid]::NewGuid())
python -E -B skill/persona-companion/scripts/persona_cli.py --data-dir $m1Data --request-file "$PWD/tests/fixtures/create_persona.json"
```

三个检查均退出 0。本地回归运行 19 例，全部通过。示例返回 `ok: true`、`status: draft` 和 `revision: 1`。

宿主验收依赖 Node.js、npm、Git、PowerShell 7 及 Python 3.11+。单独安装固定版本，再传入可执行的 Python 绝对路径。复测使用系统临时目录及单独的 `DSH_HOME`，入口在运行结束后清理自己的合成工作区。

```powershell
$m1Runtime = Join-Path ([IO.Path]::GetTempPath()) ('persona-m1-host-' + [guid]::NewGuid())
npm install --prefix $m1Runtime --registry https://registry.npmjs.org --no-audit --no-fund --save-exact @deepseek-ai/dsh@0.2.2-rc.2
$m1Python = (Get-Command python -All | Where-Object Source -NotMatch 'WindowsApps' | Select-Object -First 1).Source
pwsh -NoProfile -File tests/run_m1_harness.ps1 -Runtime $m1Runtime -Python $m1Python
```

本机通过 PATH 调用 Windows Store 的 `python.exe` 别名时，扩展探测曾退出 2 并返回 `[Errno 22] Invalid argument`。显式传入已安装的 CPython 3.12.10 可执行文件后，宿主复测退出 0。若本机 Python 位于其他路径，给 `$m1Python` 赋其绝对路径即可。

## 结果

| M1 验收项 | 证据 | 结果 |
| --- | --- | --- |
| 契约与初始迁移 | UTF-8 JSON、拒绝重复字段和非法输入、显式包外路径、schema 版本守卫与外键检查 | 通过 |
| 人物与来源隔离 | 两人物不能交叉查询、引用、采纳或复用删除 token。另一人物的附件在删除时保留 | 通过 |
| 导入与核对 | PNG/JPEG/WebP、UTF-8 文字和原文恢复。伪装格式、越界和硬链接被拒绝。目标说话人、定位及候选来源经过校验 | 通过 |
| 版本、并发与幂等 | 采纳落盘后由新进程读取。相同修订号的两个并发写只成功一次，同一请求并发重试只写一次。写入失败时原档案与修订号保留 | 通过 |
| 撤回与旧状态 | 重启后仍排除已撤回材料。支持不足的已采纳结论退出活动档案，过期提案不能采纳。未接受的临时风格不会自动启动 | 通过 |
| 崩溃和删除恢复 | 图片重命名后、数据库提交前强制结束进程。重试清除孤儿文件并只登记一份。删除附件中途注入失败，待删人物立即不可读，新进程复用原请求完成；主库无该人物正文、版本或操作正文，旧创建请求不能复活人物 | 通过 |
| 真实 Harness | 实际加载项目 `skill`，用 `pwsh` 在 `workspace-write` 下执行 CLI。完成创建、读取、导入、核对、提案、采纳、撤回、预览删除、确认删除及列表查询，验证中文、撤回结论移除和删除结果 | 通过 |

## 范围与未测项

- 文件导入使用私有 `data-dir/tmp/imports` 暂存。撤回会停用材料及派生结论，材料和历史快照保存在私有存储，整个人物删除时才随活动数据清理。删除范围由预览列出，备份、宿主历史、暂存原件及请求文件由各自的调用方管理。
- 故障验收覆盖进程强制退出和写入、删除失败注入。物理断电、性能容量目标、其他操作系统及桌面宿主仍未实测。
- 人工对话评审、模糊截图的交互式识读与确认、访谈及逐项纠正、记忆与发展、MVP 端到端和发布属于后续阶段。

