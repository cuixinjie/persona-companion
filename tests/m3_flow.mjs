import assert from 'node:assert/strict'

export const binding = turn => Object.fromEntries(
  ['session_id', 'source_turn_id', 'profile_version_id', 'turn_token'].map(key => [key, turn[key]]),
)

// 核对和采纳由合成用户脚本明确执行。真实 SDK 探测在同一流程注入模型。
export async function verifyDialogue(cli, imagePath, transcribe, generate, { expectTimeout = false } = {}) {
  for (const kind of ['chat_text', 'chat_image']) {
    const image = kind === 'chat_image'
    const name = image ? 'SYN-LIN' : 'SYN-ZHOU'
    const context = image ? '工作' : '聊天'
    const created = await cli('persona.create', { display_name: name })
    const persona = created.data.persona_id
    let revision = created.revision
    async function manage(operation, payload) {
      const result = await cli(operation, payload, persona, revision)
      revision = result.revision
      return result.data
    }
    const sourceText = '你慢慢说，我先听你说完。'
    const imported = await manage('source.import', image
      ? { kind, input_path: imagePath } : { kind, text: sourceText })
    const expectedSegments = image ? [
      { locator: 'image:1:1', speaker: 'SYN-ZHOU', text: 'Still at work?' },
      { locator: 'image:1:2', speaker: name, text: 'In a meeting. Talk later.' },
    ] : [{ locator: 'text:1', speaker: name, text: sourceText }]
    const segments = image && transcribe ? await transcribe() : expectedSegments
    assert.deepEqual(segments, expectedSegments)
    const confirmed = await manage('source.confirm', {
      source_id: imported.source_id, target_speaker: name,
      corrected_segments: segments.map(segment => ({ ...segment, context })),
    })
    const proposed = await manage('profile.propose', {
      claims: [{
        category: 'situational', origin: 'inferred', evidence_level: 'supported',
        statement: image ? '开会中曾用短句说明稍后再谈' : '这段聊天中曾请对方慢慢说并先听对方说完',
        scope: { relationship: 'unknown', period: 'unknown', context },
        evidence_ids: [confirmed.evidence_ids.at(-1)],
      }],
      unknowns: ['真实人物的个人爱好'],
    })
    const accepted = await manage('profile.accept', {
      proposal_id: proposed.proposal_id, reviewed_version: proposed.reviewed_version, fallback_accepted: true,
    })
    let { session_id } = await manage('chat.open', {})
    const prepare = async user_message =>
      (await cli('chat.prepare', { session_id, user_message, context }, persona)).data
    const verify = turn => cli('chat.validate', binding(turn), persona)
    const prepared = await prepare(image
      ? '试聊场景中，你正在开会。我问你现在方便聊吗？' : '我今天心里堵得慌，能听我慢慢说吗？')
    assert.equal(prepared.profile_version_id, accepted.profile_version_id)
    assert.equal(prepared.context_sections.source_excerpts_untrusted[0].kind, kind)
    assert.equal(prepared.context_sections.source_excerpts_untrusted[0].locator, expectedSegments.at(-1).locator)
    if (expectTimeout) {
      await assert.rejects(generate(prepared), { code: 'MODEL_DEADLINE' })
      const after = await cli('persona.get', { include_profile: true }, persona)
      assert.equal(after.revision, revision)
      assert.equal(after.data.profile.profile_version_id, accepted.profile_version_id)
      assert.deepEqual((await cli('memory.list', {}, persona)).data.memories, [])
      return
    }
    const reply = await generate?.(prepared)
    await verify(prepared)
    if (generate) assert.match(reply, image ? /稍后|晚点|晚些|回头|开完|(?:一会儿|等会儿|结束|散会).{0,6}再|later/i : /慢慢|听|说完/)
    const signal = image ? '桥边白石' : '青纸鹤'
    const agreement = await prepare(`用户提出模拟交流约定，想暂停时发“${signal}”。`)
    const saved = await cli('memory.record', {
      ...binding(agreement), kind: 'agreement', text: `用户约定在模拟交流里用“${signal}”提示暂停。`,
    }, persona)
    await verify(agreement)
    await manage('chat.close', { session_id })
    const reopened = await manage('chat.open', {})
    session_id = reopened.session_id
    const resumed = await prepare('我们在交流中约定用哪个提示词暂停？真实人物私下最喜欢什么爱好？')
    assert.equal(resumed.profile_version_id, accepted.profile_version_id)
    assert.equal(resumed.context_sections.exchange_memories_simulation[0].memory_id, saved.data.memory_id)
    const resumedReply = await generate?.(resumed)
    await verify(resumed)
    if (generate) {
      assert.ok(resumedReply.includes(signal), '新模型会话未使用已保存的提示词')
      assert.match(resumedReply, /不清楚|不知道|没有.{0,8}(依据|资料|记录)|缺乏|无法.{0,5}(确认|确定)|没法.{0,5}(确认|确定)|说不准|确定不了|保留为未知/,
        '模型未保留真实人物爱好的未知')
      assert.doesNotMatch(resumedReply, /最喜欢(画画|旅行|音乐|跑步)/)
    }
    if (!image) {
      await manage('settings.update', { memory_enabled: true, development_enabled: true })
      const candidate = await manage('development.propose', {
        patch: { category: 'interaction_habit', context, behavior: '在模拟交流中尊重用户的暂停提示词。' },
        memory_ids: [saved.data.memory_id],
      })
      assert.deepEqual((await prepare('继续试聊。')).context_sections.digital_development_simulation, [])
      await manage('development.accept', {
        proposal_id: candidate.proposal_id, reviewed_version: candidate.reviewed_version,
      })
      const developed = await prepare('继续试聊。')
      assert.equal(developed.context_sections.digital_development_simulation[0].origin, 'digital_development')
      assert.deepEqual(developed.context_sections.persona_version.claims, resumed.context_sections.persona_version.claims)
    }
  }
}
