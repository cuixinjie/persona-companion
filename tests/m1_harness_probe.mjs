import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { verifyDialogue } from './m3_flow.mjs'

export const inject = ['loader', 'agents', 'agentDefaultModel', 'appExit', 'permissionPresets', 'tools']

async function verifyConstruction(cli, dataDir) {
  const created = await cli('persona.create', { display_name: 'SYN-LIN', relationship: '朋友' })
  const persona = created.data.persona_id
  let revision = created.revision
  async function write(operation, payload, errorCode) {
    const result = await cli(operation, payload, persona, revision, errorCode)
    if (result.ok) revision = result.revision
    return result.data
  }
  const get = async () => (await cli('persona.get', { include_profile: true }, persona)).data
  const blurry = await write('source.import', {
    kind: 'chat_image', input_path: join(dataDir, 'tmp', 'imports', 'm2-blurred.png'),
  })
  const screenshot = await write('source.import', {
    kind: 'chat_image', input_path: join(dataDir, 'tmp', 'imports', 'm2-clear.png'),
    context_period: '大学',
  })
  assert.equal((await get()).evidence.length, 0)
  await write('source.confirm', {
    source_id: screenshot.source_id, target_speaker: 'unknown', corrected_segments: [],
  }, 'SPEAKER_UNKNOWN')
  const confirmed = await write('source.confirm', {
    source_id: screenshot.source_id, target_speaker: 'SYN-LIN',
    corrected_segments: [
      { locator: 'image:1:1', speaker: 'SYN-ZHOU', text: 'Still at work?' },
      { locator: 'image:1:2', speaker: 'SYN-LIN', text: 'In a meeting. Talk later.', context: '工作' },
    ],
  })
  const question = await write('question.record', {
    action: 'create', purpose: 'context', text: '私下聊天是什么样？',
  })
  const answer = await write('question.record', {
    action: 'answer', question_id: question.question_id,
    answer: { kind: 'recollection', text: '周末见面时曾经主动聊了很久。' },
  })
  const status = await get()
  assert.equal(status.sources.find(source => source.source_id === blurry.source_id).status, 'draft')
  assert.equal(status.sources.find(source => source.source_id === answer.answer_source_id).kind, 'recollection')
  assert.equal(status.questions[0].answer_source_id, answer.answer_source_id)
  assert.deepEqual(status.evidence.map(item => item.locator), ['image:1:1', 'image:1:2'])
  const claim = {
    category: 'style', statement: '在工作时用简短句子回复',
    evidence_level: 'supported', origin: 'inferred',
    scope: { relationship: '朋友', period: '大学', context: '工作' },
    evidence_ids: [confirmed.evidence_ids[1]],
  }
  await write('profile.propose', {
    claims: [{ ...claim, evidence_ids: [confirmed.evidence_ids[0]] }], unknowns: [],
  }, 'NOT_FOUND')
  await write('profile.propose', {
    claims: [{ ...claim, scope: { ...claim.scope, period: '工作后' } }], unknowns: [],
  }, 'OUTPUT_INVALID')
  const proposed = await write('profile.propose', { claims: [claim], unknowns: ['工作之外的表达'] })
  const accepted = await write('profile.accept', {
    proposal_id: proposed.proposal_id, reviewed_version: proposed.reviewed_version, fallback_accepted: false,
  })
  const correct = await write('profile.correct', {
    action: 'reject', claim_id: proposed.diff.after.claims[0].claim_id, reason: '只看得出这次正在开会',
  })
  const reviewing = await get()
  assert.equal(reviewing.profile.profile_version_id, accepted.profile_version_id)
  assert.deepEqual(reviewing.pending_proposal, correct)
  await write('profile.accept', {
    proposal_id: correct.proposal_id, reviewed_version: correct.reviewed_version, fallback_accepted: false,
  })
  const final = await get()
  assert.deepEqual(final.profile.claims, [])
  assert.equal(final.status, 'draft')
  await write('persona.update', { relationship: '共创伙伴', target_period: '毕业后' })
  const updated = await get()
  assert.equal(updated.persona_id, persona)
  assert.equal(updated.relationship, '共创伙伴')
  assert.equal(updated.target_period, '毕业后')
  await write('question.record', { action: 'stop' })
  await write('question.record', { action: 'create', purpose: 'missing', text: '私下有什么印象？' }, 'INTERVIEW_CLOSED')
  assert.equal((await get()).interview_status, 'closed')
}

async function probe(ctx) {
  await ctx.loader.await()
  const cwd = process.cwd()
  const modelSelection = ctx.agentDefaultModel.currentSelection()
  const { agent } = await ctx.agents.create({
    sessionId: 'session-' + randomUUID(),
    meta: { cwd },
    agentOptions: modelSelection,
  })
  await agent.whenIdle()
  ctx.permissionPresets.set(agent.session, 'workspace-write')
  const milestone = process.env.PERSONA_TEST_MILESTONE ?? 'M1'
  const signal = AbortSignal.timeout(180_000)
  const toolSet = await ctx.tools.resolve({ agent, modelSelection, signal })

  async function tool(name, args) {
    const { results } = await ctx.tools.execute({
      agent, toolSet, signal,
      calls: [{ type: 'tool-call', name, id: 'call-' + randomUUID(), arguments: JSON.stringify(args) }],
    })
    const result = results[0].result
    if (result.isError) throw new Error(name + ': ' + JSON.stringify(result.content))
    return result.value
  }

  try {
    const skill = await tool('skill', { name: 'persona-companion' })
    const expectedPath = join(cwd, '.dsh', 'skills', 'persona-companion')
    assert.equal(resolve(skill.resourceBase.path).toLowerCase(), expectedPath.toLowerCase())
    assert.match(skill.content, /本地操作契约/)
    const script = join(skill.resourceBase.path, 'scripts', 'persona_cli.py')
    const dataDir = join(cwd, 'private')
    const requestPath = join(cwd, 'input.json')
    const quote = value => "'" + value.replaceAll("'", "''") + "'"
    const python = process.env.PERSONA_M1_TEST_PYTHON
    assert.ok(python)

    async function cli(operation, payload, persona_id, expected_revision, errorCode) {
      const request_id = randomUUID()
      await writeFile(requestPath, JSON.stringify({
        schema_version: 1, request_id, operation, payload, persona_id, expected_revision,
      }), 'utf8')
      const command = '& ' + quote(python) + ' -E -B ' + quote(script) + ' --data-dir ' + quote(dataDir)
        + ' --request-file ' + quote(requestPath) + '; exit $LASTEXITCODE'
      const execution = await tool('pwsh', { command, description: milestone + ' 合成资料验收', workdir: cwd })
      assert.equal(execution.exitCode, errorCode ? 1 : 0, execution.stderr.text + execution.stdout.text)
      assert.equal(execution.sandbox?.mode, 'workspace-write')
      assert.equal(execution.stdout.truncated, false)
      const result = JSON.parse(execution.stdout.text)
      assert.equal(result.ok, !errorCode)
      if (errorCode) assert.equal(result.error.code, errorCode)
      assert.equal(result.request_id, request_id)
      return result
    }

    if (milestone === 'M3') {
      assert.match(skill.content, /对话指南/)
      await verifyDialogue(cli, join(dataDir, 'tmp', 'imports', 'm2-clear.png'))
      process.stdout.write(JSON.stringify({
        ok: true, milestone, skill: skill.name, tool: 'pwsh', sandbox: 'workspace-write',
        text: true, screenshot: true, resumed: true, development_review: true,
      }) + '\n')
      return
    }
    if (milestone === 'M2') {
      assert.match(skill.content, /访谈指南/)
      await verifyConstruction(cli, dataDir)
      process.stdout.write(JSON.stringify({
        ok: true, milestone, skill: skill.name, tool: 'pwsh', sandbox: 'workspace-write',
        screenshot_confirmation: true, blurry_source_still_draft: true,
        typed_question_answer: true, correction_after_review: true,
      }) + '\n')
      return
    }

    const name = 'M1 合成人物'
    const { data: { persona_id }, revision } = await cli('persona.create', { display_name: name })
    const { data } = await cli('persona.get', { include_profile: true }, persona_id)
    assert.equal(data.display_name, name)
    assert.equal(data.revision, revision)
    assert.equal(data.profile, null)
    assert.deepEqual(data.sources, [])
    const imported = await cli('source.import', { kind: 'chat_text', text: '我先听你说。' }, persona_id, revision)
    const source_id = imported.data.source_id
    const confirmed = await cli('source.confirm', {
      source_id, target_speaker: name,
      corrected_segments: [{ locator: 'text:1', speaker: name, text: '我先听你说。' }],
    }, persona_id, imported.revision)
    const proposed = await cli('profile.propose', {
      claims: [{
        category: 'style', statement: '倾向先倾听', evidence_level: 'supported', origin: 'inferred',
        scope: { relationship: 'unknown', period: 'unknown', context: '聊天' },
        evidence_ids: confirmed.data.evidence_ids,
      }],
      unknowns: ['如何道别'],
    }, persona_id, confirmed.revision)
    const accepted = await cli('profile.accept', {
      proposal_id: proposed.data.proposal_id, reviewed_version: proposed.data.reviewed_version,
      fallback_accepted: false,
    }, persona_id, proposed.revision)
    const afterAccept = await cli('persona.get', { include_profile: true }, persona_id)
    assert.equal(afterAccept.data.profile.profile_version_id, accepted.data.profile_version_id)
    assert.equal(afterAccept.data.profile.claims[0].statement, '倾向先倾听')
    const withdrawn = await cli('source.withdraw', { source_id }, persona_id, accepted.revision)
    const afterWithdraw = await cli('persona.get', { include_profile: true }, persona_id)
    assert.equal(afterWithdraw.data.profile.profile_version_id, withdrawn.data.new_version_id)
    assert.deepEqual(afterWithdraw.data.profile.claims, [])
    const preview = await cli('persona.delete.preview', {}, persona_id, withdrawn.revision)
    await cli('persona.delete.commit', { confirmation_token: preview.data.confirmation_token },
      persona_id, preview.revision)
    assert.deepEqual((await cli('persona.list', {})).data.personas, [])
    process.stdout.write(JSON.stringify({
      ok: true, skill: skill.name, tool: 'pwsh', sandbox: 'workspace-write',
      operations: [
        'persona.create', 'persona.get', 'source.import', 'source.confirm', 'profile.propose',
        'profile.accept', 'source.withdraw', 'persona.delete.preview', 'persona.delete.commit', 'persona.list',
      ],
      utf8_round_trip: true, withdrawn_claim_removed: true, deletion_confirmed: true,
    }) + '\n')
  } finally {
    toolSet.dispose()
  }
}

export function apply(ctx) {
  probe(ctx).then(() => ctx.appExit(0), error => {
    process.stderr.write(String(error.stack ?? error) + '\n')
    ctx.appExit(1)
  })
}

