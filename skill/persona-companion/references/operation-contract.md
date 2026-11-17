# 本地操作契约

由可信配置提供包外私有目录和请求文件的本地绝对路径。正文只能通过 UTF-8 JSON 文件传入，不能拼进命令。以下是 PowerShell 调用形式。

```powershell
python -E -B "<技能目录>/scripts/persona_cli.py" --data-dir "<私有目录>" --request-file "<请求 JSON>"
```

请求包含 `schema_version: 1`、`request_id`（规范小写 UUID）、`operation`、`payload`。除 create/list 外还须提供 `persona_id`。写操作须提供 `expected_revision`，create 与 `memory.record` 除外。成功输出 `ok`、`request_id`、`data`、`revision`，退出 0。失败输出 `ok: false` 与 `error`（`code`、`message`、`retryable`、`details`），业务错误退出 1，缺少启动参数退出 2。修订号冲突时先重新读取；同一写请求的重试复用原请求及 ID，响应可能早于当前状态。新请求使用新 ID。

| 操作 | payload | 返回与效果 |
| --- | --- | --- |
| persona.create | `display_name`（1–80 字），可选 `relationship`、`target_period`、`address_terms`（`user_to_persona`、`persona_to_user`） | 新人物 ID、修订号及默认开关。未填的关系、时期及称呼为 `unknown` |
| persona.update | `relationship`、`target_period` 至少一项，可填自定义值或 `unknown` | 同一人物 ID、生效字段和活动版本 ID |
| persona.list | `{}` | 可用人物摘要 |
| persona.get | 可选 `include_profile`，默认 false | 人物、访谈状态和设置。为 true 时另含活动档案、有效待审提案、已采纳纠正、访谈问题、当前材料和证据。空活动档案或空提案返回 null |
| source.import | `kind`、`text` 或 `input_path` 二选一，可选 `context_period` | `source_id`、`status: draft` |
| source.confirm | `source_id`、`target_speaker`、`corrected_segments` | 已核对证据 ID。须至少确认一段目标说话人内容 |
| profile.propose | `claims`、`unknowns`（字符串数组） | `proposal_id`、`reviewed_version`、前后对照、`validation_issues`。提案包含完整候选档案 |
| profile.accept | `proposal_id`、`reviewed_version`、`fallback_accepted`（布尔） | 新档案版本 ID 和版本号。须经用户审阅，提案修订号须仍为当前修订号 |
| profile.correct | `claim_id`、`action`、`reason`、按操作决定是否提供 `replacement`，以及可选的 `apply_immediately` | 前后对照、纠正对象、`proposal_id` 和 `reviewed_version`。立即纠正亦返回新档案版本 ID 和版本号 |
| question.record | `action`，其余字段按下表 | `question_id`、状态，回答时返回草稿来源 ID |
| source.withdraw | `source_id` | 失效结论 ID、新版本 ID（若活动档案有变化） |
| persona.delete.preview | `{}` | 删除范围与排除项、10 分钟有效的 `confirmation_token`。不推进人物修订号 |
| persona.delete.commit | `confirmation_token` | 经用户确认后删除。失败时保留原请求文件并以原请求重试，直至返回 `status: deleted` |

导入文本类型为 `chat_text`、`recollection`、`hypothetical`、`style_preference`；后面三类使用 `text`。`chat_image` 使用 `input_path`。文件导入先由调用方将用户选定的文件暂存于 `data-dir/tmp/imports`，再传入绝对路径。支持 PNG/JPEG/WebP（10 MiB）和 UTF-8 `.txt`（2 MiB）；行内正文也限制 2 MiB。暂存原件与请求文件由调用方清理。人物删除会清理技能管理的附件和 SQLite 内容，备份及宿主历史独立管理。

`corrected_segments` 每项需要 `locator`、`text`、`speaker`。定位格式为文本段 `text:1`，或图片与消息序号 `image:1:1`。可选 `context`、带时区的 ISO 8601 `occurred_at`（可为空）、`event_key`（用户核对的事件标识）。同一事件使用同一标识。模糊或归属不清的内容留待核对。

`persona.update` 使用最新 `expected_revision`。在已有活动版本的人物上变更关系或时期时，沿用已采纳结论和各自范围，创建新版本，归档旧交流摘要并让旧数字发展退出对话。旧轮次与旧待审提案失效。新范围缺少适用已知结论且用户尚未接受临时风格时，回到草稿。重启后以 `persona.get` 读取更新结果。

`claims` 每项示例。`evidence_ids` 为支持证据，可选 `contradicting_evidence_ids` 为反例。

```json
{
  "category": "style",
  "statement": "在日常聊天中倾向先听对方说",
  "evidence_level": "supported",
  "origin": "inferred",
  "scope": {"relationship": "朋友", "period": "unknown", "context": "日常聊天"},
  "evidence_ids": ["b972b644-019e-44d2-a2c7-249773c38090"]
}
```

类别为 `style/situational/relational/trait/fact`，证据程度为 `supported/tentative/unknown`，来源为 `inferred/recalled/user_defined`。仅可引用本人物已核对的目标说话人证据。假设回答只可支持 `tentative` 的 `situational` 候选。人为偏好只可作为 `user_defined` 的风格设定。`supported` 的稳定性格须引用至少两个不同的已标注事件。没有已知结论时必须明确接受临时风格；撤回导致已知结论清空时，沿用已有的接受状态，未接受则回到草稿。

结论的 `scope.period` 和所引历史材料的 `context_period` 都明确时，标签必须相同。冲突时提案返回 `OUTPUT_INVALID`，详情含 `field=scope.period`、`claim_period`、`evidence_periods`。旧待审提案有冲突时无法采纳，`persona.get` 返回 `proposal_needing_review_id`。活动版的 `period_conflict_claim_ids` 标记旧版中待复核的结论。

### 访谈

| `action` | 其他字段 | 结果 |
| --- | --- | --- |
| `create` | `purpose` 为 `missing/conflict/context/counterexample`，`text`，可选 `target_claim_ids`（来自本人物当前活动版本或有效待审提案） | `pending` |
| `answer` | `question_id`、`answer: {kind, text, context_period?}`，kind 为 `recollection/hypothetical/style_preference` | `answered`，`answer_source_id` 对应待核对草稿。使用 `source.confirm` 核对后才产生证据 |
| `skip` | `question_id` | `skipped` |
| `stop` | 无 | `interview_status=closed`，余下待答题转为跳过 |
| `resume` | 无 | `interview_status=open`，恢复提问权限 |

只可回答或跳过待答问题。相同问题仍待答时返回 `QUESTION_PENDING`，已回答时返回 `QUESTION_CLOSED`。对已跳过的问题，CLI 比对规范化文字，之后若没有新增并仍有效的已确认历史来源，则返回 `QUESTION_SKIPPED`。同义改写及材料是否与原问题相关由 skill 判断。回答来源撤回后，问题保留已答状态，读取得到的来源 ID 为空。每次成功写入均推进人物 revision，有旧提案时须重新展示新提案。

访谈状态按人物持久化，默认 `open`。处于 `closed` 时创建任意访谈问题均返回 `INTERVIEW_CLOSED`。状态变化后，原会话可重新准备回复，状态变化前签发的轮次失效。

### 逐条纠正

`profile.correct` 的目标必须是本人物的活动结论。`action=replace` 必须提供一个符合 `claims` 项结构的 `replacement`。`action=reject` 删除活动档案中的该条结论并保留审计，省略 `replacement`。两种操作均需要用户请求对应的 `reason`，未涉及的结论 ID、证据和未知项保留。

`apply_immediately` 默认 false，返回可用 `profile.accept` 采纳的纠正提案。只有用户明确要求立即生效时才传 true，CLI 同事务提交新版本并保留既有临时风格设置。纠正移除最后一条已知结论且临时风格未获接受时，人物回到草稿。重启后的 `applied_corrections` 记录已生效的纠正，其中 `previous_claim` 用于识别已删除或替换的内容。

仅在取得用户当前确认时执行核对、采纳和删除。管理写入会使旧修订号上的待审版本过期。撤回后的旧快照保存在私有主库。`persona.get` 返回当前档案和未撤回材料，活动档案的 `development` 单列已采纳的数字发展。

### 对话、交流记忆与数字发展

| 操作 | payload | 成功结果 |
| --- | --- | --- |
| `chat.open` | 空，须给 `expected_revision` | `session_id`。人物须已就绪 |
| `chat.close` | `session_id`，须给 `expected_revision` | `status=closed`，结束旧会话并使待发轮次失效 |
| `chat.prepare` | `session_id`、`user_message`（最多 4,000 字）、可选 `context`（最多 160 字，默认 `unknown`）；关系或目标时期未知时可传 `relationship`、`period` | 轮次绑定、`context_sections`、上下文预算信息和当前开关 |
| `chat.validate` | 轮次绑定 | 已核验的会话、轮次、版本和当下能否写入记忆 |
| `memory.record` | 轮次绑定、`kind`、`text`（最多 500 字），省略 `expected_revision` | `status=saved` 和 `memory_id`，或 `status=skipped_disabled` |
| `memory.list` | 可选 `before_id`、`limit`（默认 20，最多 50） | 最新优先，含 `active/archived` 状态及下一页游标 `next_before_id` |
| `settings.update` | 两个布尔值 `memory_enabled`、`development_enabled` | 当前开关、失效候选 ID，活动旧记忆可继续用于交流 |
| `development.list` | 空 | 已采纳的发展、当前待审候选和开关 |
| `development.propose` | `patch: {category, context, behavior}` 和 1–12 个本人物的活动 `memory_ids` | 待审候选及 `diff.before/after` |
| `development.accept` / `development.reject` | `proposal_id`、`reviewed_version` | 采纳或拒绝状态，采纳返回新人物版本 |

`chat.prepare`、`chat.validate`、`memory.list`、`development.list` 是读取，省略 `expected_revision`。会话开关和记忆写入走幂等事务，人物管理修订号保持原值。给活动人物准备对话时，`context` 使用与档案约定一致的情境标签。当前关系、目标时期和情境用于筛选，档案的 `unknown` 范围可通用。材料只含匹配档案结论引用的已核对目标人物片段；省略项列在 `omitted_for_budget`，`more_memories` 提示存在更多历史摘要。`context_token_estimate` 以编码后的 UTF-8 字节数保守估算，实际消耗由模型 tokenizer 决定。

`context_sections.interview_status` 用于区分访谈状态。`closed` 时回复规则要求保留未知并直接交流。试聊还会排除任何与结论或请求的已知时期冲突的历史结论，即使结论已在旧版采纳或其范围曾标为 `unknown`。

轮次绑定为 `session_id`、`source_turn_id`、`profile_version_id`、`turn_token`，原样从 `chat.prepare` 复制。绑定有效期 10 分钟，人物删除、会话结束、活动版本变化后不得发送旧草稿。生成回复由宿主完成，待发文本和当前消息不写入技能数据库。记忆种类为 `user_update/shared_exchange/agreement`。同一轮同一摘要的重试返回同一记录，改变内容则冲突。通过当轮及当前记忆开关核验才可新增。写操作的幂等回执只证明原操作结果，回复前始终另调 `chat.validate`。

发展类别为 `interaction_habit/simulated_trait`。情境及类别相同的采纳会替换该模拟表现，其他已采纳项保留。`behavior` 描述模拟中的交流行为，最多 500 字。相同类别、情境与同组交流依据的候选被拒绝后，不可再次提交。关闭发展后待审候选失效。历史档案更新后，已有交流摘要归档，旧发展层保留于历史版本供追溯，退出活动上下文。模拟记忆 ID 无法用作任何历史结论的证据。
