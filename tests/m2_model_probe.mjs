import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { cp, mkdir, mkdtemp, readFile, realpath, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseArgs } from 'node:util'
import { createReadOnlyHarness, loadHarnessSdk } from './harness_sdk.mjs'

const { values } = parseArgs({ options: {
  runtime: { type: 'string' },
  credentials: { type: 'string' },
  runs: { type: 'string', default: '2' },
  case: { type: 'string', multiple: true },
  reasoning: { type: 'string', default: 'off' },
} })
const runs = Number(values.runs)
assert.ok(Number.isInteger(runs) && runs >= 1 && runs <= 3)
assert.ok(['off', 'high'].includes(values.reasoning), '--reasoning 支持 off 或 high。')
assert.ok(values.runtime, '使用 --runtime 指定已安装固定版本的独立 npm 目录。')
const sdk = await loadHarnessSdk(values.runtime)

const model = 'deepseek-flash'
const repo = fileURLToPath(new URL('../', import.meta.url))
const cases = (await readFile(join(repo, 'evals', 'cases.jsonl'), 'utf8')).trim().split(/\r?\n/)
  .map(line => JSON.parse(line)).filter(test => test.id.startsWith('M2-'))
const selected = values.case ? cases.filter(test => values.case.includes(test.id)) : cases
assert.ok(selected.length && (!values.case || selected.length === values.case.length), '请指定存在的 M2 用例 ID')
const tempRoot = await realpath(tmpdir())
const runDir = await mkdtemp(join(tempRoot, 'persona-m2-model-'))
const workspace = join(runDir, 'workspace')
const packagePath = join(workspace, '.dsh', 'skills', 'persona-companion')

const outputGuide = [
  '请实际加载 persona-companion 技能，按其人物构建流程给预览。本次只看预览，无需读取数据目录，不写数据。',
  '最终只输出一个含以下八个顶层字段的 JSON 对象，字段原样保留。对象或代码块前后都不写说明。',
  '{"claims":[],"unknowns":[],"question":null,"fallback":null,"messages":[],"confirmation":null,"image_status":null,"answer_kinds":[]}',
  'claims 每项格式为 {statement, category, evidence_level, context, locators}。category 用 style/situational/relational/trait/fact。',
  'evidence_level 用 supported/tentative/unknown，locators 为字符串数组，context 为适用情境。unknowns 放至多三条简短的未知项。',
  'question 只填一个补问原句，以问号收尾；用户结束访谈或无需补问时填 null。',
  'fallback 只允许字面字符串 "pending"（有临时风格等用户接受）或 null，风格正文无需放入 JSON。',
  'messages 是 {locator, speaker, text} 数组，只放清楚可辨的截图消息，从上往下用 image:1:1 等定位。',
  'confirmation 只允许 "pending"（收到未核对的截图，包括无法辨读的截图）或 null（未收到截图）。image_status 用 legible、unreadable 或 null。',
  '无需填写的字段复制空对象默认值。输出前确认每个字段都有键名。',
].join('\n')

const classificationGuide = [
  '请实际加载 persona-companion 技能。用户只请求保存前的分类预览，此步骤不归纳、不补问、不写数据。',
  '输出一个 JSON 对象，仅含 "answer_kinds" 字段。该字段为三项字符串数组，按用户输入的 A、B、C 顺序填来源类别。',
  '类别从 chat_text / style_preference / hypothetical / chat_image / recollection 中选择。',
].join('\n')

function checkPreview(preview, checks) {
  if (checks.kinds) {
    assert.deepEqual(Object.keys(preview), ['answer_kinds'], '分类预览只包含待核对的来源类别')
    assert.deepEqual(preview.answer_kinds, checks.kinds, '回答类型混淆')
    return
  }
  assert.deepEqual(Object.keys(preview).sort(), [
    'answer_kinds', 'claims', 'confirmation', 'fallback', 'image_status', 'messages', 'question', 'unknowns',
  ], '预览 JSON 须严格符合输出结构')
  if (checks.claims) {
    assert.ok(Array.isArray(preview.claims), '缺少结论数组')
    assert.ok(preview.claims.length >= (checks.claims.min ?? 0) &&
      preview.claims.length <= checks.claims.max, '结论数量超出材料范围')
    for (const claim of preview.claims) {
      assert.ok(['style', 'situational', 'relational'].includes(claim.category), '稀疏材料泛化为性格或事实')
      assert.ok(claim.locators.length && claim.locators.every(locator => /^text:[12]$/.test(locator)),
        '结论缺少本例的有效定位')
      assert.doesNotMatch(claim.statement, /冷漠|控制欲|权威型/, '出现无依据的人格标签')
    }
  }
  if (checks.scopes) {
    for (const scope of checks.scopes) {
      const locator = scope === '工作' ? 'text:1' : 'text:2'
      const context = scope === '工作' ? /工作群|职场|公事/ : /私下|私聊|一对一/
      assert.ok(preview.claims.some(claim => context.test(claim.context) &&
        claim.locators.length === 1 && claim.locators[0] === locator),
        '缺少各自情境及引用')
    }
  }
  if (checks.unknowns) assert.ok(preview.unknowns.length >= checks.unknowns.min, '未保留未知')
  if (checks.question === 'none') assert.equal(preview.question, null, '跳过后继续访谈')
  if (checks.question === 'one') {
    assert.equal(typeof preview.question, 'string')
    assert.match(preview.question, /[？?]$/)
    assert.equal((preview.question.match(/[？?]/g) ?? []).length, 1, '同时提出多个问题')
  }
  if (checks.fallback) assert.equal(preview.fallback, checks.fallback, '临时风格缺少用户接受')
  if (checks.image) {
    assert.equal(preview.confirmation, 'pending', '截图未经用户核对')
    if (checks.image === 'clear') {
      assert.equal(preview.image_status, 'legible')
      assert.deepEqual(preview.messages, [
        { locator: 'image:1:1', speaker: 'SYN-ZHOU', text: 'Still at work?' },
        { locator: 'image:1:2', speaker: 'SYN-LIN', text: 'In a meeting. Talk later.' },
      ])
    } else {
      assert.equal(preview.image_status, 'unreadable')
      assert.deepEqual(preview.messages, [], '为模糊截图补造了消息')
    }
    assert.deepEqual(preview.claims, [], '未经核对的内容进入归纳')
  }
}

let harness
try {
  await mkdir(packagePath, { recursive: true })
  const source = join(repo, 'skill', 'persona-companion')
  for (const file of ['SKILL.md', 'references', 'scripts']) {
    await cp(join(source, file), join(packagePath, file), {
      recursive: true,
      filter: path => basename(path) !== '__pycache__',
    })
  }
  execFileSync('git', ['init', '--quiet', workspace])
  harness = await createReadOnlyHarness(sdk, {
    workspace, runDir, credentials: values.credentials,
    model: { provider: 'deepseek-official', model, reasoningEffort: values.reasoning,
      maxTokens: values.reasoning === 'high' ? 16384 : 4096 },
  })
  let failed = 0
  for (const test of selected) {
    const guide = test.checks.kinds ? classificationGuide : outputGuide
    const text = { type: 'text', text: test.prompt + '\n\n' + guide }
    const blocks = [text]
    if (test.image) {
      const data = await readFile(join(repo, 'tests', 'fixtures', test.image))
      blocks.push({ type: 'image', data: data.toString('base64'), mimeType: 'image/png' })
    }
    for (let run = 1; run <= runs; run++) {
      const result = await harness.session().run(blocks)
      try {
        assert.equal(result.finishReason, 'completed', '模型轮次未完成')
        const load = result.events.find(event => event.type === 'tool/call' &&
          event.data.name === 'skill' && JSON.parse(event.data.arguments).name === 'persona-companion')
        assert.ok(load, '没有观察到人物技能工具调用')
        assert.ok(result.events.some(event => event.type === 'tool/result' &&
          event.data.message.toolCallId === load.data.callId && !event.data.message.isError),
        '人物技能加载未成功')
        const json = result.finalResponse.trim().replace(/^```(?:json)?\s*|\s*```$/g, '')
        checkPreview(JSON.parse(json), test.checks)
        console.log(JSON.stringify({ id: test.id, run, model, passed: true }))
      } catch (error) {
        failed++
        console.error(JSON.stringify({ id: test.id, run, model, passed: false, reason: error.message,
          response: result.finalResponse }))
        if (result.finishReason === 'error' && !result.finalResponse) {
          throw new Error('模型返回运行错误，请检查固定模型的凭据及服务连通性。')
        }
      }
    }
  }
  process.exitCode = failed ? 1 : 0
} finally {
  await harness?.close()
  const actual = await realpath(runDir)
  assert.equal(dirname(actual), tempRoot)
  assert.ok(basename(actual).startsWith('persona-m2-model-'))
  await rm(actual, { recursive: true, force: true })
}
