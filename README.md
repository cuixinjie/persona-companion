# ai 数字人（persona-companion）

面向 DeepSeek Harness 的本地人物陪伴 skill：基于用户提供的聊天截图、文字记录与回忆，形成有依据、可纠正的人物初稿，并在资料不足时使用用户知情的临时风格交流。未知保留为未知，模拟内容不当作真实人物的事实。

## 当前状态

可从核对过的截图或文字建立人物，采纳档案后试聊，并在新会话中从私有库恢复当前档案和活动交流摘要。数字发展须单独审阅采纳，历史证据独立保存。

M4 的本地安装包、已执行检查及人工验收状态见 [M4 验收记录](docs/acceptance.md)。从源码生成本地包可运行 `python -B tools/build_package.py`，输出到 `dist/persona-companion-m4.zip`。首次安装和升级见 [安装说明](docs/install.md)。

在项目根目录可用合成请求体验创建人物。每次使用独立的系统临时目录。

```powershell
$m1Data = Join-Path ([IO.Path]::GetTempPath()) ('persona-m1-example-' + [guid]::NewGuid())
python -E -B skill/persona-companion/scripts/persona_cli.py --data-dir $m1Data --request-file "$PWD/tests/fixtures/create_persona.json"
```

完整的已实现操作见 [本地操作契约](skill/persona-companion/references/operation-contract.md)。模型和截图能力实测见 [M0 兼容性验收报告](docs/compatibility.md)。

Harness 中可用文字和截图完成核对、试聊与跨会话恢复。按[合成示例](docs/synthetic-example.md)体验。

## 项目文档

- 产品与范围：[docs/01-需求与范围.md](docs/01-需求与范围.md)
- 交互设计：[docs/02-产品与交互设计.md](docs/02-产品与交互设计.md)
- 系统架构与目录：[docs/03-系统架构.md](docs/03-系统架构.md)
- 技术契约：[docs/04-技术详细设计.md](docs/04-技术详细设计.md)
- 开发计划与验收：[docs/05-开发计划与验收.md](docs/05-开发计划与验收.md)
- AI 协作与工程约束：[docs/06-AI协作与工程约束.md](docs/06-AI协作与工程约束.md)

## 数据与隐私

真实资料存放在 skill 包外的私有 data-dir。宿主及配置的模型服务会处理生成回复所需的输入，删除人物只覆盖技能管理的数据。详见[隐私说明](docs/privacy.md)。

## 许可证

待定（Q02），见 [docs/01-需求与范围.md](docs/01-需求与范围.md) 第 8 节。
