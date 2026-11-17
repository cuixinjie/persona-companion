# 安装 persona-companion

## 环境

已验证组合为 Windows 11 x64、PowerShell 7、Git、Node.js 24.15.0、npm 11.12.1、Python 3.12.10 和 DeepSeek Harness 0.2.2-rc.2。技能脚本使用 Python 3.11+ 标准库。宿主要求 Node.js 24.9.0+。需要通过 Harness 配置支持文本、工具调用和图片输入的模型。实测使用 DeepSeek 官方 `deepseek-flash`，可用范围见源码项目的兼容性记录。

## 在新项目中安装

以下 PowerShell 命令从 ZIP 所在目录执行。每次创建独立工作区和独立宿主目录。将第一行的 ZIP 名称替换为手中的文件名。

```powershell
$personaZip = (Resolve-Path -LiteralPath './persona-companion-m4.zip').Path
$personaRoot = Join-Path ([IO.Path]::GetTempPath()) ('persona-demo-' + [guid]::NewGuid())
$personaFiles = Join-Path $personaRoot 'extracted'
$personaWorkspace = Join-Path $personaRoot 'workspace'
$personaSkills = Join-Path $personaWorkspace '.dsh/skills'
$personaRuntime = Join-Path $personaRoot 'runtime'
$personaData = Join-Path $personaWorkspace 'private'
New-Item -ItemType Directory -Path $personaSkills | Out-Null
git -C $personaWorkspace init --quiet
Expand-Archive -LiteralPath $personaZip -DestinationPath $personaFiles
Copy-Item -LiteralPath (Join-Path $personaFiles 'persona-companion') -Destination $personaSkills -Recurse
npm install --prefix $personaRuntime --registry https://registry.npmjs.org --no-audit --no-fund --save-exact @deepseek-ai/dsh@0.2.2-rc.2
$personaDsh = Join-Path $personaRuntime 'node_modules/@deepseek-ai/dsh/lib/bin.js'
node $personaDsh --version
```

确认版本输出 `0.2.2-rc.2`。先用包内的合成 JSON 执行一次本地创建，成功时返回 `"ok":true` 和人物 ID。此命令可在其他工作目录执行。

```powershell
$personaCli = Join-Path $personaSkills 'persona-companion/scripts/persona_cli.py'
$personaRequest = Join-Path $personaFiles 'examples/create_persona.json'
python -E -B $personaCli --data-dir $personaData --request-file $personaRequest
$personaData
```

用独立宿主 home 和工作区写入权限启动。按宿主提示配置该环境的模型服务和凭据。凭据由宿主管理。

```powershell
Push-Location -LiteralPath $personaWorkspace
$personaPreviousHome = $env:DSH_HOME
$personaPreviousMode = $env:DSH_PERMISSION_MODE
try {
    $env:DSH_HOME = Join-Path $personaRoot 'home'
    $env:DSH_PERMISSION_MODE = 'workspace-write'
    node $personaDsh
} finally {
    $env:DSH_HOME = $personaPreviousHome
    $env:DSH_PERMISSION_MODE = $personaPreviousMode
    Pop-Location
}
```

告诉宿主「加载 persona-companion。私有数据目录为上一步输出的绝对路径。用已创建的合成人物先走一遍文字流程。」按[合成示例](synthetic-example.md)完成核对、采纳、试聊、恢复。截图需要通过宿主支持的方式选作本轮图像输入，文件见 `examples/synthetic-chat.png`。

安装到现有 Git 项目时，将整个 `persona-companion` 目录放入项目的 `.dsh/skills/`。技能、参考文件、脚本和迁移一起更新。宿主从最近 Git 根发现项目技能。Windows 若报 `SANDBOX_PREPARATION_FAILED`，先按上面的临时目录步骤验证。CLI 数据路径接受本机绝对路径，拒绝链接、重解析点和技能包内部位置。工作区内使用真实数据时，先确保该目录处于合适的宿主写入范围并被 Git 忽略。

## 升级与回退

当前 SQLite `user_version` 为 4，请求 JSON 的 `schema_version` 为 1。停掉正在使用该数据目录的会话和脚本。保留旧技能包，并用 Python 的 `sqlite3.Connection.backup()` 生成一致性数据库备份，连同对应附件保存到独立私有位置。先在备份副本上启动新版。旧库会在事务内升级，迁移失败时可从成套备份恢复。需要回退时，暂停写入，另存新版数据后成套恢复旧技能、数据库和附件。检查备份中是否含后来删除的人物，并在重新启用原环境前重新执行对应删除。

随包提供的[隐私说明](privacy.md)说明模型处理、记录保留和删除范围。
