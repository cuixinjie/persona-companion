import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { cp, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { createReadOnlyHarness, loadHarnessSdk } from './harness_sdk.mjs'
import { verifyDialogue } from './m3_flow.mjs'

const { values } = parseArgs({ options: {
  runtime: { type: 'string' }, python: { type: 'string' }, credentials: { type: 'string' },
  extracted: { type: 'string' }, 'simulate-timeout': { type: 'boolean' }, 'interview-stop': { type: 'boolean' },
} })
assert.ok(values.runtime && values.python, '需要 --runtime 和 --python。')
const python = await realpath(values.python)
const sdk = await loadHarnessSdk(values.runtime)

const tempRoot = await realpath(tmpdir())
const runDir = await mkdtemp(join(tempRoot, 'persona-m3-model-'))
const workspace = join(runDir, 'workspace')
const source = values.extracted ? join(resolve(values.extracted), 'persona-companion')
  : fileURLToPath(new URL('../skill/persona-companion/', import.meta.url))
const syntheticImage = values.extracted ? join(resolve(values.extracted), 'examples', 'synthetic-chat.png')
  : fileURLToPath(new URL('./fixtures/m2-clear.png', import.meta.url))
const packagePath = join(workspace, '.dsh', 'skills', 'persona-companion')
const script = join(packagePath, 'scripts', 'persona_cli.py')
const dataDir = join(workspace, 'private')
const inbox = join(dataDir, 'tmp', 'imports')
const screenshot = join(inbox, 'm2-clear.png')
const requestPath = join(workspace, 'request.json')
let harness
const forceTimeout = values['simulate-timeout']

async function waitForReply(pending, milliseconds) {
  let timer
  try {
    return await Promise.race([pending, new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error('模型等待超时。'), { code: 'MODEL_DEADLINE' })), milliseconds)
    })])
  } finally {
    clearTimeout(timer)
  }
}

try {
  await cp(source, packagePath, { recursive: true, filter: path => basename(path) !== '__pycache__' })
  await mkdir(inbox, { recursive: true })
  await cp(syntheticImage, screenshot)
  execFileSync('git', ['init', '--quiet', workspace])
  harness = await createReadOnlyHarness(sdk, {
    workspace, runDir, credentials: values.credentials,
    model: { provider: 'deepseek-official', model: 'deepseek-flash', reasoningEffort: 'off', maxTokens: 4096 },
  })

  async function call(operation, payload, persona_id, expected_revision) {
    const request_id = randomUUID()
    await writeFile(requestPath, JSON.stringify({
      schema_version: 1, request_id, operation, payload, persona_id, expected_revision,
    }))
    try {
      const result = JSON.parse(execFileSync(python, [
        '-E', '-B', script, '--data-dir', dataDir, '--request-file', requestPath,
      ], { cwd: workspace, encoding: 'utf8', timeout: 15_000 }))
      assert.ok(result.ok)
      assert.equal(result.request_id, request_id)
      return result
    } finally {
      await rm(requestPath)
    }
  }

  async function model(stage, text, image) {
    const blocks = [{ type: 'text', text }]
    if (image) blocks.push({ type: 'image', data: image.toString('base64'), mimeType: 'image/png' })
    await harness.start()
    const result = await waitForReply(harness.session().run(blocks), forceTimeout ? 100 : 120_000)
    assert.equal(result.finishReason, 'completed', stage + ' 模型轮次未完成')
    const skill = result.events.find(event => event.type === 'tool/call' && event.data.name === 'skill' &&
      JSON.parse(event.data.arguments).name === 'persona-companion')
    assert.ok(skill, stage + ' 缺少技能调用')
    assert.ok(result.events.some(event => event.type === 'tool/result' &&
      event.data.message.toolCallId === skill.data.callId && !event.data.message.isError), stage + ' 技能加载失败')
    console.log(JSON.stringify({ stage, model: 'deepseek-flash', skill_loaded: true }))
    return result.finalResponse.trim().replace(/^```(?:json)?\s*|\s*```$/g, '')
  }

  async function transcribe() {
    const text = await model('screenshot', [
      '请实际加载 persona-companion。验收驱动只提供合成截图，模拟用户将另行核对，当前仅识读预览。',
      '只输出 JSON 数组，元素为 {locator, speaker, text}，从上到下编号 image:1:1、image:1:2。',
      'speaker 和 text 按图片上可见原文，不翻译、不猜测、不归纳、不调用写入工具。',
    ].join('\n'), await readFile(screenshot))
    return JSON.parse(text)
  }

  async function generate(prepared) {
    const sections = prepared.context_sections
    return model(sections.exchange_memories_simulation.length ? 'resumed-reply' : 'trial-reply', [
      '请实际加载 persona-companion 并阅读对话指南。这是合成验收，驱动已调用 chat.prepare 并会在展示前核验绑定。',
      '把下方结构化结果作为唯一人物上下文，按照其中 user_message 生成一段简体中文待发回复。',
      '只输出拟发送的自然回复，保持简洁。当前模型只生成待发文本，全部本地读写及核验由驱动处理。',
      JSON.stringify(sections),
    ].join('\n'))
  }

  if (values['interview-stop']) {
    assert.ok(!forceTimeout, '请单独运行访谈或超时用例。')
    const created = await call('persona.create', { display_name: '合成阿林', relationship: '父亲' })
    const persona = created.data.persona_id
    const question = await call('question.record', {
      action: 'create', purpose: 'missing', text: '他哪一次安慰过你？',
    }, persona, created.revision)
    const stopped = await call('question.record', { action: 'stop' }, persona, question.revision)
    const proposal = await call('profile.propose', {
      claims: [], unknowns: ['他以前最喜欢什么', '小时候共同去过的地方'],
    }, persona, stopped.revision)
    const accepted = await call('profile.accept', {
      proposal_id: proposal.data.proposal_id, reviewed_version: proposal.data.reviewed_version,
      fallback_accepted: true,
    }, persona, proposal.revision)
    const session = await call('chat.open', {}, persona, accepted.revision)
    for (const [index, user_message] of [
      '我记不起来了，先聊聊。真实的他以前最喜欢什么？我们小时候去过哪里？',
      '我暂时想不起那些事，先说说话吧。他小时候常和我去哪儿？',
    ].entries()) {
      const { data } = await call('chat.prepare', { ...session.data, user_message }, persona)
      assert.equal(data.context_sections.interview_status, 'closed')
      const reply = await generate(data)
      console.log(JSON.stringify({ scenario: 'T03', run: index + 1, reply }))
      assert.match(reply, /不清楚|不知道|没有|缺乏|没法|无法|记不清|不能|不确定/)
      assert.doesNotMatch(reply, /(?:记得|想起|回忆|经历|印象|最常做|当年|小时候|往事)[^。！\n]{0,35}[？?]/)
      assert.doesNotMatch(reply, /(?:请|可以)(?:你|您)?(?:回忆|补充).{0,16}(?:经历|往事|当时|真实)/)
      const { session_id, source_turn_id, profile_version_id, turn_token } = data
      await call('chat.validate', { session_id, source_turn_id, profile_version_id, turn_token }, persona)
    }
  } else {
    await verifyDialogue(call, screenshot, transcribe, generate, { expectTimeout: forceTimeout })
    console.log(JSON.stringify(forceTimeout
      ? { ok: true, simulated_client_deadline_ms: 100, active_profile_preserved: true, no_new_memory: true }
      : { ok: true, text: true, screenshot: true, replies: 4,
        resumed: true, unknowns_preserved: true, development_review: true }))
  }
} finally {
  await harness?.close()
  const actual = await realpath(runDir)
  assert.equal(dirname(actual), tempRoot)
  assert.ok(basename(actual).startsWith('persona-m3-model-'))
  await rm(actual, { recursive: true, force: true })
}
