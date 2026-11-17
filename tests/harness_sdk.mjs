import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { readFile, realpath, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

export const HARNESS_VERSION = '0.2.2-rc.2'

export async function loadHarnessSdk(runtime) {
  const root = resolve(runtime)
  const require = createRequire(join(root, 'package.json'))
  const packages = join(root, 'node_modules', '@deepseek-ai')
  const dshBin = join(packages, 'dsh', 'lib', 'bin.js')
  assert.equal(execFileSync(process.execPath, [dshBin, '--version'], { encoding: 'utf8' }).trim(), HARNESS_VERSION)
  const sdkPackage = JSON.parse(await readFile(join(packages, 'dsh-sdk-client', 'package.json'), 'utf8'))
  assert.equal(sdkPackage.version, HARNESS_VERSION)
  const { DeepSeekHarness } = await import(pathToFileURL(require.resolve('@deepseek-ai/dsh-sdk-client')))
  return { DeepSeekHarness, dshBin }
}

export async function createReadOnlyHarness({ DeepSeekHarness, dshBin }, { workspace, runDir, credentials, model }) {
  const patches = []
  if (credentials) {
    const patch = join(runDir, 'credentials-location.yml')
    await writeFile(patch, JSON.stringify([
      { id: 'credentials', config: { path: await realpath(credentials), watch: false } },
    ]))
    patches.push(patch)
  }
  return new DeepSeekHarness({
    cwd: workspace, dshBin, dshHome: join(runDir, 'home'), profile: 'sdk', patches, ...model,
    initializeTimeoutMs: 30_000, requestTimeoutMs: 120_000,
    env: { ...process.env, DSH_TELEMETRY_DISABLED: '1', DSH_PERMISSION_MODE: 'read-only' },
  })
}
