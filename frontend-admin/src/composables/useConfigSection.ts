import { ref, computed } from 'vue'

export function useConfigSection<T>(options: {
  loadFn: () => Promise<T>,
  saveFn: (data: T) => Promise<void>,
  initialValue?: T,
}) {
  const loading = ref(false)
  const saving = ref(false)
  const data = ref<T>(options.initialValue as T)
  const original = ref<string>('')
  const dirty = computed(() => JSON.stringify(data.value) !== original.value)

  async function load() {
    loading.value = true
    try {
      const result = await options.loadFn()
      data.value = result as any
      original.value = JSON.stringify(result)
    } finally {
      loading.value = false
    }
  }

  async function save() {
    saving.value = true
    try {
      await options.saveFn(data.value as T)
      original.value = JSON.stringify(data.value)
    } finally {
      saving.value = false
    }
  }

  function reset() {
    data.value = JSON.parse(original.value)
  }

  return { loading, saving, data, dirty, load, save, reset }
}
