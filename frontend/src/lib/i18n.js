import { readonly, ref } from 'vue'
import { englishMessages } from './messages.js'

export const LOCALE_STORAGE_KEY = 'saasbridge.frontend.locale'

// Chinese source messages also provide a readable fallback for untranslated data.
// Keep the language preference separate from backend credentials and sessions.
export function createI18n({ storage, page } = {}) {
  if (storage === undefined) {
    try { storage = globalThis.localStorage } catch { storage = null }
  }
  if (page === undefined) page = globalThis.document
  let saved
  try { saved = storage?.getItem(LOCALE_STORAGE_KEY) } catch { /* Private browsing may block storage. */ }
  const locale = ref(saved === 'en' ? 'en' : 'zh')

  function t(message, params = {}) {
    const source = String(message ?? '')
    const translated = locale.value === 'en' && Object.hasOwn(englishMessages, source)
      ? englishMessages[source] : source
    return translated.replace(/\{(\w+)\}/g, (placeholder, name) =>
      Object.hasOwn(params, name) ? String(params[name]) : placeholder)
  }

  function updateDocument() {
    if (!page) return
    page.documentElement.lang = locale.value === 'en' ? 'en' : 'zh-CN'
    page.title = t('SaaSBridge 客服调试工作台')
  }

  function setLocale(value) {
    locale.value = value === 'en' ? 'en' : 'zh'
    try { storage?.setItem(LOCALE_STORAGE_KEY, locale.value) } catch { /* Switching still works without persistence. */ }
    updateDocument()
  }

  updateDocument()
  return { locale: readonly(locale), t, setLocale }
}

export const { locale, t, setLocale } = createI18n()
