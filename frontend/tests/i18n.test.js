import test from 'node:test'
import assert from 'node:assert/strict'
import { computed } from 'vue'
import { createI18n, LOCALE_STORAGE_KEY } from '../src/lib/i18n.js'
import { englishMessages } from '../src/lib/messages.js'

function storageWith(values = {}) {
  const data = new Map(Object.entries(values))
  return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), data }
}
function page() { return { documentElement: { lang: '' }, title: '' } }

test('first visit and unsupported saved languages default to Chinese', () => {
  for (const saved of [null, 'fr', 'EN', '{}']) {
    const document = page()
    const i18n = createI18n({ storage: storageWith({ [LOCALE_STORAGE_KEY]: saved }), page: document })
    assert.equal(i18n.locale.value, 'zh')
    assert.equal(i18n.t('知识库'), '知识库')
    assert.equal(document.documentElement.lang, 'zh-CN')
    assert.equal(document.title, 'SaaSBridge 客服调试工作台')
  }
})

test('a language selection survives reload without touching connection settings', () => {
  const storage = storageWith({ 'saasbridge.frontend.settings': 'unchanged-session' })
  const i18n = createI18n({ storage, page: page() })
  i18n.setLocale('en')
  const document = page()
  const reloaded = createI18n({ storage, page: document })
  assert.equal(reloaded.t('知识库'), 'Knowledge base')
  assert.equal(document.documentElement.lang, 'en')
  assert.equal(document.title, 'SaaSBridge Support Console')
  reloaded.setLocale('zh')
  assert.equal(storage.getItem(LOCALE_STORAGE_KEY), 'zh')
  assert.equal(storage.getItem('saasbridge.frontend.settings'), 'unchanged-session')
  assert.equal(storage.data.size, 2)
})

test('labels update reactively in both directions without remounting', () => {
  const i18n = createI18n({ storage: null, page: page() })
  const label = computed(() => i18n.t('运行详情'))
  assert.equal(label.value, '运行详情')
  i18n.setLocale('en')
  assert.equal(label.value, 'Run details')
  i18n.setLocale('zh')
  assert.equal(label.value, '运行详情')
})

test('blocked browser storage does not prevent switching language', () => {
  const storage = {
    getItem() { throw new Error('Storage blocked') },
    setItem() { throw new Error('Storage full') }
  }
  const i18n = createI18n({ storage, page: page() })
  assert.equal(i18n.locale.value, 'zh')
  assert.doesNotThrow(() => i18n.setLocale('en'))
  assert.equal(i18n.t('连接配置'), 'Connection settings')
})

test('dynamic counts and filenames are substituted as literal content', () => {
  const i18n = createI18n({ storage: null, page: page() })
  assert.equal(i18n.t('{count} 条消息', { count: 3 }), '3 条消息')
  i18n.setLocale('en')
  assert.equal(i18n.t('{count} 条消息', { count: 3 }), 'Messages: 3')
  assert.equal(i18n.t('{name} 导入成功', { name: '{count}.yaml' }), '{count}.yaml imported successfully')
  assert.equal(i18n.t('unrecognized API detail'), 'unrecognized API detail')
})

test('all translations preserve their interpolation parameters', () => {
  const params = text => [...text.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort()
  for (const [source, translated] of Object.entries(englishMessages)) {
    assert.ok(translated.trim(), `Empty translation: ${source}`)
    assert.deepEqual(params(source), params(translated), `Mismatched parameters: ${source}`)
  }
})
