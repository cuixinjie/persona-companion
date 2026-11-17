# M0 兼容性验收记录

M1 的 Python 运行时、真实宿主沙箱结果和新验收入口见 [M1 验收记录](m1-acceptance.md)。
M2 沿用固定版本，并对真实宿主中的人物构建及模型预览执行复测，见 [M2 验收记录](m2-acceptance.md)。
M3 沿用固定版本，在宿主沙箱和真实模型中验证文字与截图试聊及恢复，见 [M3 验收记录](m3-acceptance.md)。
M4 重新安装固定版本的 npm 宿主和 SDK，从安装 ZIP 解压后测试宿主发现、沙箱操作、文字和截图流程。实际环境及边界见 [M4 验收记录](acceptance.md)。

2026-11-12。**M0 验收通过。** DeepSeek Harness 0.2.2-rc.2 在 Windows npm 环境通过技能加载、沙箱脚本执行、配置与隐私检查。官方模型 `deepseek-flash` 通过真实工具调用和合成截图识读。

## 固定环境

| 项目 | 实测值 |
| --- | --- |
| Harness | npm `@deepseek-ai/dsh@0.2.2-rc.2`，tag `dsh-v0.2.2-rc.2`，commit `ddac96760b6edf8fa0fe17cbdcf0b512f4c12b2c` |
| SDK | npm `@deepseek-ai/dsh-sdk-client@0.2.2-rc.2`，使用同版本宿主的 `sdk` profile |
| 操作系统 | Windows 11 家庭版 x64，build 26200 |
| 运行时 | Node.js 24.15.0、npm 11.12.1、PowerShell 7.6.9 |
| Python | CPython 3.13.14，构建 `fd17997`，64 位，MSC v.1944 |
| SQLite | 3.50.4，由该 Python 的 `sqlite3` 提供 |
| 模型 | DeepSeek 官方服务 `https://api.deepseek.com/anthropic`，provider `deepseek-official`，模型 ID `deepseek-flash`。目标版本自带模型目录将其标为 DeepSeek-V4.1-Flash，声明文本与图像输入 |
| 参数 | SDK `maxTokens=2048`，推理档位与温度沿用宿主默认。文本和图像使用独立会话，每次验收各提交一次。图像为 700×230 的合成 PNG |

核对依据：[官方版本发布](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.2.2-rc.2)、[该版本安装说明](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.2/README.md)、[技能发现与加载](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.2/docs/subsystems/skills.md)、[SDK 图像输入](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.2/packages/sdk/client/README.md)、[模型目录](https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.2/packages/llm/llm-deepseek/src/models.ts)。

## 当时的验收方法

以下命令保留为验收证据。测试脚本、合成夹具和本地凭据配置已按要求清理，原入口已退役。验收依赖 Node.js ≥ 24.9.0、npm、Git、PowerShell 7 和 Python ≥ 3.11。全局 `dsh` 当时为 0.1.1-rc.2，验收使用独立安装的固定版本。

```powershell
npm install --prefix .test-data/m0/runtime --registry https://registry.npmjs.org --no-audit --no-fund --save-exact @deepseek-ai/dsh@0.2.2-rc.2 @deepseek-ai/dsh-sdk-client@0.2.2-rc.2
pwsh -NoProfile -File tests/run_m0.ps1
```

验收入口在系统临时目录新建带独立 Git 根的工作区，把完整技能包安装到 `workspace/.dsh/skills/persona-companion/`。该版本从最近的 Git 根发现项目技能。测试使用单独的 `DSH_HOME`，打印此次运行目录。项目 E 盘路径上的初次尝试在沙箱准备时遇到 `SANDBOX_PREPARATION_FAILED`，原始错误为 `SetNamedSecurityInfoW failed (Win32 5): grantWrite(...)`。换用系统临时目录后，默认 `workspace-write` 沙箱通过。

宿主检查使用 headless profile 和测试 overlay 直接调度真实宿主的 `skill`、`pwsh` 工具。调度过程仅执行本地探测。探测脚本使用宿主返回的技能包路径、显式的合成数据目录和请求文件，实际执行等价于：

```powershell
python -I -B "<技能包>/scripts/m0_probe.py" --data-dir "<测试工作区>/data" --request-file "<测试工作区>/data/m0_probe.json"
```

模型复测从项目忽略目录的 `.test-data/m0/.env` 读取用户配置的密钥。以下命令当时将合成提示和图片发送到 DeepSeek 官方模型服务。

```powershell
pwsh -NoProfile -File tests/run_m0.ps1 -Model
```

`-Model` 仅在文本回合实际调用并成功返回 `skill` 和 `pwsh` 结果，且独立图像回合未调用工具就返回图片中的完整验证码时退出 0。模型或断言失败退出 1。验证码仅出现在合成截图中，图像提示未给出答案。合成测试工作区和 Harness home 由入口生成。

## 实测结果

| 检查 | 结果与证据 |
| --- | --- |
| 固定版本与启动 | npm 安装退出 0。本地 `dsh --version` 输出 `0.2.2-rc.2`，`dsh --profile headless --help` 退出 0 |
| 本地 Python | `python -m compileall -q skill/persona-companion/scripts` 退出 0。直接执行探测退出 0，中文 JSON 经 SQLite 参数绑定写入内存并读回一致 |
| 业务单元测试 | `python -m unittest discover -s tests -p 'test_*.py' -v` 发现 0 个用例，退出 1。现有业务用例文件为后续阶段占位 |
| 真实宿主技能发现与加载 | 通过。目录来源为 `project-dsh`，真实 `skill` 工具返回完整说明及隔离工作区内技能目录。SDK 模型回合的会话事件也记录了含 `persona-companion` 的技能目录 |
| 真实宿主脚本执行 | 通过。`pwsh` 工具在 `workspace-write` 下执行 Python，退出 0，输出 `{"ok":true,"operation":"m0_probe","python":"3.13.14","sqlite":"3.50.4","utf8_round_trip":true}` |
| 配置与路径 | 通过。请求指向 data-dir 外的已存在合成文件时退出 1，返回 `INVALID_TEST_PATH`。缺少 `--data-dir` 时退出 2。宿主检查总退出码为 0 |
| 本地隐私检查 | 通过。探测只接受固定合成请求，输出不回显正文。data-dir 中只保留原请求文件，未生成磁盘数据库。测试运行使用临时工作区，模型端使用独立会话历史 |
| SDK 图像输入 | 通过本地接收。PNG 进入 `user/message` 的图像块，独立 `DSH_HOME` 下生成附件，附件字节与原图的 SHA-256 一致（`8993bbc6c22d98878954b3a370146db934a6ffea2e540315ba2af7b6f7f14726`） |
| 真实模型文本和工具调用 | 通过。模型回合为 `completed`，会话事件确认 `skill` 和 `pwsh` 均成功，Python 探测返回 `utf8_round_trip: true` |
| 真实模型截图识读 | 通过。独立图像回合为 `completed`，SDK 接收图像，模型仅根据图片准确返回 `M0-EA9C645D`，会话无工具调用 |
| 复测入口 | `pwsh -NoProfile -File tests/run_m0.ps1 -Model` 退出 0，`model-text` 与 `model-image` 均为 `passed: true` |

首次模型测试因旧密钥返回 HTTP 401 / `AUTH`，空配置的隔离复测返回 `MISSING_CREDENTIAL` 并退出 1。命令审批曾拒绝复制桌面凭据文件（`blocked by policy`）。用户在项目忽略目录的 `.test-data/m0/.env` 填入有效密钥后，使用同一验收入口完成复测。

## 隐私边界与待验项

- 探测脚本只读合成 JSON，SQLite 使用 `:memory:`。发送给 Harness 的消息、工具结果和图片可能保存在其会话与附件目录。模型验收会把合成内容交给指定服务处理。项目包和验收记录没有包含真实人物资料或密钥。
- Q01 已确认指定宿主、模型与 SDK 路径支持文本工具调用及合成截图识读。
- Q03 当前无需接入额外 OCR 或外部识图服务。今后如需引入新的资料接收方，仍须按需求约束确认。
- M0 使用 Windows npm 宿主的 headless 与 SDK profiles。后续阶段复测范围见各阶段验收记录。桌面端附件交互及其他操作系统仍待验证。
