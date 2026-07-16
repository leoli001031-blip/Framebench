import { useCallback, useEffect, useMemo, useState } from "react"
import {
  archiveOrphanJobDir,
  getBackendLog,
  getDataDiagnostics,
  listSettings,
  testConnectivity,
  testImageConnectivity,
  updateSetting,
} from "@/lib/api"
import { downloadFile } from "@/lib/utils"
import type { DataDiagnostics, OrphanJobDir } from "@/types"

type SettingsTab = "ai" | "local" | "data"
type EngineName = "analysis" | "storyboard" | "image"
type EngineState = "unconfigured" | "configured" | "testing" | "success" | "error"

interface EngineCheck {
  state: EngineState
  message: string
  checkedAt?: string
}

const engineConfig: Record<EngineName, {
  title: string
  description: string
  secretKey: string
  modelKey: string
  urlKey: string
  defaultModel: string
}> = {
  analysis: {
    title: "拉片分析",
    description: "逐镜头画面分析与全片综述",
    secretKey: "analysis_api_key",
    modelKey: "analysis_model",
    urlKey: "analysis_base_url",
    defaultModel: "step-3.7-flash",
  },
  storyboard: {
    title: "分镜创作",
    description: "生成分镜脚本与生图提示词",
    secretKey: "storyboard_api_key",
    modelKey: "storyboard_model",
    urlKey: "storyboard_base_url",
    defaultModel: "step-3.7-flash",
  },
  image: {
    title: "分镜图生成",
    description: "生成分镜预览图",
    secretKey: "image_api_key",
    modelKey: "image_model",
    urlKey: "image_base_url",
    defaultModel: "step-image-edit-2",
  },
}

const engineNames = Object.keys(engineConfig) as EngineName[]
const editableKeys = [
  ...engineNames.flatMap((name) => [engineConfig[name].modelKey, engineConfig[name].urlKey]),
  "whisper_model",
]

const defaultSettingValues: Record<string, string> = {
  analysis_model: "step-3.7-flash",
  analysis_base_url: "https://api.stepfun.com/v1",
  storyboard_model: "step-3.7-flash",
  storyboard_base_url: "https://api.stepfun.com/v1",
  image_model: "step-image-edit-2",
  image_base_url: "https://api.stepfun.com/v1",
  whisper_model: "base",
}

const whisperOptions = [
  { value: "tiny", label: "tiny", description: "速度最快，适合快速草稿" },
  { value: "base", label: "base（推荐）", description: "速度与准确率平衡" },
  { value: "small", label: "small", description: "准确率更高，处理时间更长" },
  { value: "medium", label: "medium", description: "高准确率，需要更多内存" },
  { value: "large", label: "large", description: "最高质量，耗时与内存占用最大" },
]
const whisperModels = new Set(whisperOptions.map((option) => option.value))

function emptyEngineStrings(): Record<EngineName, string> {
  return Object.fromEntries(engineNames.map((name) => [name, ""])) as Record<EngineName, string>
}

function emptyEngineFlags(): Record<EngineName, boolean> {
  return Object.fromEntries(engineNames.map((name) => [name, false])) as Record<EngineName, boolean>
}

function providerHost(value: string): string {
  try {
    return new URL(value).hostname || "自定义供应商"
  } catch {
    return "自定义供应商"
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
}

function formatCheckedAt(value?: string): string {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
}

function configuredState(configured: boolean): EngineCheck {
  return configured
    ? { state: "configured", message: "已配置，尚未验证" }
    : { state: "unconfigured", message: "未配置密钥" }
}

function validateHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return (url.protocol === "https:" || url.protocol === "http:") && Boolean(url.host) && !url.username && !url.password
  } catch {
    return false
  }
}

function statusPresentation(check: EngineCheck) {
  switch (check.state) {
    case "success":
      return { label: "连接正常", className: "border-sage/35 bg-sage/10 text-sage" }
    case "error":
      return { label: "连接失败", className: "border-clay/35 bg-clay/10 text-clay" }
    case "testing":
      return { label: "正在测试", className: "border-primary/25 bg-primary-soft/35 text-primary" }
    case "configured":
      return { label: "已配置 · 未验证", className: "border-primary/20 bg-primary-soft/25 text-primary" }
    default:
      return { label: "未配置", className: "border-line/40 bg-paper text-muted" }
  }
}

function EngineStatus({ check }: { check: EngineCheck }) {
  const presentation = statusPresentation(check)
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-semibold ${presentation.className}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden="true" />
      {presentation.label}
    </span>
  )
}

function orphanContents(item: OrphanJobDir): string[] {
  const contents = []
  if (item.has_original) contents.push("原片")
  if (item.has_report) contents.push("报告")
  if (item.has_playback) contents.push("播放代理")
  return contents
}

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<SettingsTab>("ai")
  const [initialValues, setInitialValues] = useState<Record<string, string>>({})
  const [localValues, setLocalValues] = useState<Record<string, string>>({})
  const [secretPresence, setSecretPresence] = useState<Record<EngineName, boolean>>({
    analysis: false,
    storyboard: false,
    image: false,
  })
  const [apiKeyInputs, setApiKeyInputs] = useState<Record<EngineName, string>>(emptyEngineStrings)
  const [clearSecrets, setClearSecrets] = useState<Record<EngineName, boolean>>(emptyEngineFlags)
  const [engineChecks, setEngineChecks] = useState<Record<EngineName, EngineCheck>>({
    analysis: configuredState(false),
    storyboard: configuredState(false),
    image: configuredState(false),
  })
  const [diagnostics, setDiagnostics] = useState<DataDiagnostics | null>(null)
  const [diagnosticsError, setDiagnosticsError] = useState("")
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState("")
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState("")
  const [saveSuccess, setSaveSuccess] = useState("")
  const [pendingArchiveId, setPendingArchiveId] = useState<string | null>(null)
  const [archivingOrphan, setArchivingOrphan] = useState<string | null>(null)
  const [archiveResult, setArchiveResult] = useState("")

  const loadSettings = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true)
    setLoadError("")
    try {
      const data = await listSettings()
      const byKey = new Map(data.map((setting) => [setting.key, setting]))
      const values: Record<string, string> = {}
      editableKeys.forEach((key) => {
        values[key] = byKey.get(key)?.value || defaultSettingValues[key] || ""
      })
      const presence = Object.fromEntries(engineNames.map((name) => [
        name,
        Boolean(byKey.get(engineConfig[name].secretKey)?.value),
      ])) as Record<EngineName, boolean>

      setInitialValues(values)
      setLocalValues(values)
      setSecretPresence(presence)
      setApiKeyInputs(emptyEngineStrings())
      setClearSecrets(emptyEngineFlags())
      setEngineChecks(Object.fromEntries(engineNames.map((name) => [name, configuredState(presence[name])])) as Record<EngineName, EngineCheck>)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "配置读取失败")
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [])

  const loadDiagnostics = useCallback(async () => {
    setDiagnosticsError("")
    try {
      setDiagnostics(await getDataDiagnostics())
    } catch (error) {
      setDiagnosticsError(error instanceof Error ? error.message : "数据体检失败")
    }
  }, [])

  useEffect(() => {
    void Promise.resolve().then(async () => {
      await Promise.all([loadSettings(), loadDiagnostics()])
    })
  }, [loadDiagnostics, loadSettings])

  const dirtyKeys = useMemo(
    () => editableKeys.filter((key) => (localValues[key] || "") !== (initialValues[key] || "")),
    [initialValues, localValues],
  )
  const secretsDirty = engineNames.some((name) => Boolean(apiKeyInputs[name].trim()) || clearSecrets[name])
  const hasUnsavedChanges = dirtyKeys.length > 0 || secretsDirty
  const configuredEngineCount = engineNames.filter((name) => (
    !clearSecrets[name] && (Boolean(apiKeyInputs[name].trim()) || secretPresence[name])
  )).length

  const setEngineUnverified = useCallback((name: EngineName, configured?: boolean) => {
    const hasKey = configured ?? (
      !clearSecrets[name] && (Boolean(apiKeyInputs[name].trim()) || secretPresence[name])
    )
    setEngineChecks((previous) => ({ ...previous, [name]: configuredState(hasKey) }))
  }, [apiKeyInputs, clearSecrets, secretPresence])

  const handleValueChange = (key: string, value: string) => {
    setLocalValues((previous) => ({ ...previous, [key]: value }))
    setSaveError("")
    setSaveSuccess("")
    const engine = engineNames.find((name) => engineConfig[name].modelKey === key || engineConfig[name].urlKey === key)
    if (engine) setEngineUnverified(engine)
  }

  const handleApiKeyChange = (name: EngineName, value: string) => {
    setApiKeyInputs((previous) => ({ ...previous, [name]: value }))
    setClearSecrets((previous) => ({ ...previous, [name]: false }))
    setSaveError("")
    setSaveSuccess("")
    setEngineChecks((previous) => ({
      ...previous,
      [name]: configuredState(Boolean(value.trim()) || secretPresence[name]),
    }))
  }

  const handleStageSecretClear = (name: EngineName) => {
    if (!window.confirm(`保存后将移除${engineConfig[name].title}的 API 密钥。确定继续吗？`)) return
    setApiKeyInputs((previous) => ({ ...previous, [name]: "" }))
    setClearSecrets((previous) => ({ ...previous, [name]: true }))
    setSaveError("")
    setSaveSuccess("")
    setEngineChecks((previous) => ({ ...previous, [name]: configuredState(false) }))
  }

  const validateSettings = (): string => {
    for (const name of engineNames) {
      const config = engineConfig[name]
      if (!(localValues[config.modelKey] || "").trim()) return `${config.title}的模型标识不能为空`
      if (!validateHttpUrl(localValues[config.urlKey] || "")) return `${config.title}的接口地址无效`
    }
    if (!whisperModels.has(localValues.whisper_model || "")) return "请选择有效的 Whisper 模型"
    return ""
  }

  const handleSave = async () => {
    if (!hasUnsavedChanges || saving) return
    const validationError = validateSettings()
    if (validationError) {
      setSaveError(validationError)
      return
    }

    setSaving(true)
    setSaveError("")
    setSaveSuccess("")
    try {
      for (const name of engineNames) {
        const key = engineConfig[name].secretKey
        if (clearSecrets[name]) {
          await updateSetting(key, "")
        } else if (apiKeyInputs[name].trim()) {
          await updateSetting(key, apiKeyInputs[name].trim())
        }
      }
      for (const key of dirtyKeys) await updateSetting(key, (localValues[key] || "").trim())
      await loadSettings(false)
      setSaveSuccess("所有更改已保存")
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "保存失败")
    } finally {
      setSaving(false)
    }
  }

  const handleTestEngine = async (name: EngineName) => {
    const config = engineConfig[name]
    const currentApiKey = apiKeyInputs[name].trim()
    const hasCurrentKey = Boolean(currentApiKey) || (!clearSecrets[name] && secretPresence[name])
    if (!hasCurrentKey) {
      setEngineChecks((previous) => ({
        ...previous,
        [name]: { state: "error", message: "请先输入 API 密钥" },
      }))
      return
    }

    setEngineChecks((previous) => ({ ...previous, [name]: { state: "testing", message: "正在测试当前输入" } }))
    try {
      const payload = {
        engine: name,
        api_key: currentApiKey || undefined,
        model: localValues[config.modelKey] || config.defaultModel,
        base_url: localValues[config.urlKey] || defaultSettingValues[config.urlKey],
      }
      const result = name === "image"
        ? await testImageConnectivity(payload)
        : await testConnectivity(payload)
      setEngineChecks((previous) => ({
        ...previous,
        [name]: { state: "success", message: result.message, checkedAt: result.checked_at },
      }))
    } catch (error) {
      setEngineChecks((previous) => ({
        ...previous,
        [name]: { state: "error", message: error instanceof Error ? error.message : "连接测试失败" },
      }))
    }
  }

  const handleExportLog = async () => {
    setDiagnosticsError("")
    try {
      const content = await getBackendLog()
      downloadFile(content, `framebench-backend-log-${new Date().toISOString().slice(0, 10)}.txt`, "text/plain")
    } catch (error) {
      setDiagnosticsError(error instanceof Error ? error.message : "日志导出失败")
    }
  }

  const handleArchiveOrphan = async (id: string) => {
    setArchivingOrphan(id)
    setDiagnosticsError("")
    setArchiveResult("")
    try {
      const result = await archiveOrphanJobDir(id)
      setArchiveResult(`已移入归档：${result.archived_path}`)
      setPendingArchiveId(null)
      await loadDiagnostics()
    } catch (error) {
      setDiagnosticsError(error instanceof Error ? error.message : "残留目录归档失败")
    } finally {
      setArchivingOrphan(null)
    }
  }

  if (loading) return <div className="py-20 text-center text-sm text-muted animate-pulse">正在读取配置...</div>

  if (loadError) {
    return (
      <div className="mx-auto max-w-lg py-24 text-center">
        <p className="text-sm font-semibold text-clay">{loadError}</p>
        <button onClick={() => void loadSettings()} className="mt-5 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-white focus-visible:ring-2 focus-visible:ring-primary/40">
          重试
        </button>
      </div>
    )
  }

  const whisperValue = localValues.whisper_model || ""
  const whisperDescription = whisperOptions.find((option) => option.value === whisperValue)?.description
  const diagnosticsAttentionCount = diagnostics
    ? diagnostics.deleted_jobs + diagnostics.orphan_job_dirs.length + diagnostics.duplicate_filenames.length
    : 0

  return (
    <div className="mx-auto max-w-4xl pb-24">
      <header className="mb-4">
        <h1 className="font-serif text-2xl font-medium text-ink">设置</h1>
        <p className="mt-1 text-sm text-muted">管理模型连接、本地处理与数据维护</p>
      </header>

      <div role="tablist" aria-label="设置分类" className="mb-4 grid grid-cols-3 gap-1 rounded-lg border border-line/30 bg-surface p-1">
        {([
          ["ai", "AI 与模型"],
          ["local", "本地处理"],
          ["data", `数据与维护${diagnosticsAttentionCount ? ` · ${diagnosticsAttentionCount}` : ""}`],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={activeTab === value}
            onClick={() => setActiveTab(value)}
            className={`min-h-9 rounded-md px-3 text-sm font-semibold transition-colors focus-visible:ring-2 focus-visible:ring-primary/35 ${
              activeTab === value ? "bg-primary text-white" : "text-muted hover:bg-paper hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {activeTab === "ai" && (
        <section aria-label="AI 与模型" className="overflow-hidden rounded-lg border border-line/25 bg-surface">
          <div className="border-b border-line/20 p-4">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="font-serif text-xl font-medium text-ink">AI 服务连接</h2>
                <p className="mt-1 text-sm text-muted">
                  分析、分镜与生图可分别连接不同的 OpenAI 兼容供应商
                </p>
              </div>
              <span className={`rounded-md border px-2.5 py-1 text-xs font-semibold ${configuredEngineCount === 3 && !hasUnsavedChanges ? "border-sage/35 bg-sage/10 text-sage" : "border-primary/20 bg-primary-soft/25 text-primary"}`}>
                {hasUnsavedChanges ? "有未保存更改" : `${configuredEngineCount} / 3 已配置`}
              </span>
            </div>
          </div>

          <div className="divide-y divide-line/20">
            {engineNames.map((name) => {
              const config = engineConfig[name]
              const check = engineChecks[name]
              const baseUrl = localValues[config.urlKey] || defaultSettingValues[config.urlKey]
              return (
                <div key={name} className="p-4 sm:p-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2.5">
                        <h3 className="text-base font-semibold text-ink">{config.title}</h3>
                        <EngineStatus check={check} />
                      </div>
                      <p className="mt-0.5 text-sm text-muted">
                        {config.description} · {providerHost(baseUrl)} · {localValues[config.modelKey] || config.defaultModel}
                      </p>
                      {(check.state === "testing" || check.state === "success" || check.state === "error") && (
                        <p className={`mt-0.5 text-xs font-medium ${check.state === "error" ? "text-clay" : check.state === "success" ? "text-sage" : "text-muted"}`}>
                          {check.message}
                          {check.checkedAt ? ` · ${formatCheckedAt(check.checkedAt)}` : ""}
                        </p>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => void handleTestEngine(name)}
                      disabled={check.state === "testing"}
                      className="min-h-9 shrink-0 rounded-md border border-line/40 px-4 text-sm font-semibold text-ink hover:border-primary/40 hover:bg-paper disabled:cursor-wait disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-primary/30"
                    >
                      测试当前输入
                    </button>
                  </div>

                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <label className="text-sm font-medium text-muted">
                      模型标识
                      <input
                        value={localValues[config.modelKey] || ""}
                        onChange={(event) => handleValueChange(config.modelKey, event.target.value)}
                        placeholder="model-name"
                        className="mt-1.5 min-h-11 w-full rounded-md border border-line/35 bg-surface px-3 font-mono text-sm text-ink focus:border-primary/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
                      />
                    </label>
                    <label className="text-sm font-medium text-muted">
                      API Base URL
                      <input
                        value={localValues[config.urlKey] || ""}
                        onChange={(event) => handleValueChange(config.urlKey, event.target.value)}
                        placeholder="https://provider.example/v1"
                        className="mt-1.5 min-h-11 w-full rounded-md border border-line/35 bg-surface px-3 font-mono text-sm text-ink focus:border-primary/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
                      />
                    </label>
                    <label className="text-sm font-medium text-muted sm:col-span-2" htmlFor={`${name}-api-key`}>
                      API 密钥
                      <div className="mt-1.5 flex flex-col gap-2 sm:flex-row">
                        <input
                          id={`${name}-api-key`}
                          type="password"
                          autoComplete="off"
                          value={apiKeyInputs[name]}
                          onChange={(event) => handleApiKeyChange(name, event.target.value)}
                          placeholder={clearSecrets[name]
                            ? "保存后移除密钥；输入新密钥可撤销"
                            : secretPresence[name]
                              ? "留空保留已保存密钥"
                              : "输入该供应商的 API 密钥"}
                          className="min-h-11 flex-1 rounded-md border border-line/35 bg-surface px-3.5 text-sm text-ink placeholder:text-muted focus:border-primary/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
                        />
                        {clearSecrets[name] ? (
                          <button
                            type="button"
                            onClick={() => {
                              setClearSecrets((previous) => ({ ...previous, [name]: false }))
                              setEngineChecks((previous) => ({ ...previous, [name]: configuredState(secretPresence[name]) }))
                            }}
                            className="min-h-11 rounded-md border border-line/35 px-4 text-sm font-semibold text-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-primary/30"
                          >
                            撤销移除
                          </button>
                        ) : secretPresence[name] ? (
                          <button
                            type="button"
                            onClick={() => handleStageSecretClear(name)}
                            className="min-h-11 rounded-md border border-clay/30 px-4 text-sm font-semibold text-clay hover:bg-clay/5 focus-visible:ring-2 focus-visible:ring-clay/30"
                          >
                            移除密钥
                          </button>
                        ) : null}
                      </div>
                    </label>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {activeTab === "local" && (
        <section aria-label="本地处理" className="rounded-lg border border-line/25 bg-surface p-5 sm:p-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="font-serif text-xl font-medium text-ink">本地转写</h2>
              <p className="mt-1 text-sm text-muted">Whisper 在本机提取音频文字</p>
            </div>
            <span className={`rounded-md border px-2.5 py-1 text-xs font-semibold ${whisperModels.has(whisperValue) ? "border-sage/35 bg-sage/10 text-sage" : "border-clay/35 bg-clay/10 text-clay"}`}>
              {whisperModels.has(whisperValue) ? "模型有效" : "模型无效"}
            </span>
          </div>

          <label htmlFor="whisper-model" className="mt-6 block text-sm font-semibold text-ink">Whisper 模型</label>
          <select
            id="whisper-model"
            value={whisperModels.has(whisperValue) ? whisperValue : ""}
            onChange={(event) => handleValueChange("whisper_model", event.target.value)}
            className="mt-2 min-h-11 w-full rounded-md border border-line/35 bg-paper/55 px-3.5 text-sm text-ink focus:border-primary/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/25"
          >
            {!whisperModels.has(whisperValue) && <option value="" disabled>当前值无效，请重新选择</option>}
            {whisperOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <p className="mt-2 text-sm text-muted">{whisperDescription || `当前保存值“${whisperValue || "空"}”无效`}</p>
        </section>
      )}

      {activeTab === "data" && (
        <section aria-label="数据与维护" className="space-y-5">
          <div className="rounded-lg border border-line/25 bg-surface p-5 sm:p-6">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="font-serif text-xl font-medium text-ink">数据状态</h2>
                <p className="mt-1 text-sm text-muted">
                  {diagnostics ? `${diagnostics.active_jobs} 个素材，${diagnosticsAttentionCount} 项可整理内容` : "正在读取本地数据"}
                </p>
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => void handleExportLog()} className="min-h-10 rounded-md border border-line/35 px-3.5 text-sm font-semibold text-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-primary/30">导出日志</button>
                <button type="button" onClick={() => void loadDiagnostics()} className="min-h-10 rounded-md border border-line/35 px-3.5 text-sm font-semibold text-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-primary/30">刷新</button>
              </div>
            </div>

            {diagnosticsError && <p className="mt-4 rounded-md border border-clay/25 bg-clay/5 px-3 py-2 text-sm font-medium text-clay">{diagnosticsError}</p>}
            {archiveResult && <p className="mt-4 break-all rounded-md border border-sage/25 bg-sage/5 px-3 py-2 text-sm font-medium text-sage">{archiveResult}</p>}

            {diagnostics && (
              <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  ["素材", diagnostics.active_jobs],
                  ["回收站", diagnostics.deleted_jobs],
                  ["残留目录", diagnostics.orphan_job_dirs.length],
                  ["数据结构", `v${diagnostics.schema_version}`],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-md border border-line/20 bg-paper/55 px-3.5 py-3">
                    <p className="text-xs font-semibold text-muted">{label}</p>
                    <p className="mt-1 font-serif text-xl text-ink">{value}</p>
                  </div>
                ))}
              </div>
            )}
          </div>

          {diagnostics && diagnostics.orphan_job_dirs.length > 0 && (
            <div className="rounded-lg border border-line/25 bg-surface p-5 sm:p-6">
              <h3 className="text-base font-semibold text-ink">残留目录</h3>
              <p className="mt-1 break-all text-sm text-muted">确认后移动到：{diagnostics.orphan_archive_dir}</p>
              <div className="mt-4 divide-y divide-line/20">
                {diagnostics.orphan_job_dirs.map((item) => {
                  const contents = orphanContents(item)
                  const isPending = pendingArchiveId === item.id
                  return (
                    <div key={item.id} className="py-4 first:pt-0 last:pb-0">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                        <div className="min-w-0">
                          <p className="truncate font-mono text-sm text-ink" title={item.id}>{item.id}</p>
                          <p className="mt-1 text-sm text-muted">{formatBytes(item.size_bytes)} · {item.file_count} 个文件</p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {(contents.length > 0 ? contents : ["无原片、报告或播放代理"]).map((content) => (
                              <span key={content} className="rounded border border-line/30 bg-paper px-2 py-0.5 text-xs font-medium text-muted">{content}</span>
                            ))}
                          </div>
                        </div>
                        {!isPending && (
                          <button
                            type="button"
                            onClick={() => setPendingArchiveId(item.id)}
                            className="min-h-9 shrink-0 rounded-md border border-line/40 px-3 text-sm font-semibold text-muted hover:border-primary/35 hover:text-ink focus-visible:ring-2 focus-visible:ring-primary/30"
                          >
                            移入归档
                          </button>
                        )}
                      </div>
                      {isPending && (
                        <div className="mt-3 rounded-md border border-clay/25 bg-clay/5 p-3">
                          <p className="text-sm font-semibold text-ink">确认移动这个目录？</p>
                          <p className="mt-1 text-sm text-muted">文件不会永久删除，将完整移动到残留归档目录。</p>
                          <div className="mt-3 flex justify-end gap-2">
                            <button type="button" onClick={() => setPendingArchiveId(null)} className="min-h-9 rounded-md px-3 text-sm font-semibold text-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-primary/30">取消</button>
                            <button
                              type="button"
                              onClick={() => void handleArchiveOrphan(item.id)}
                              disabled={archivingOrphan !== null}
                              className="min-h-9 rounded-md bg-clay px-3 text-sm font-semibold text-white disabled:cursor-wait disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-clay/35"
                            >
                              {archivingOrphan === item.id ? "正在移动" : "确认移入归档"}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {diagnostics && diagnostics.duplicate_filenames.length > 0 && (
            <div className="rounded-lg border border-line/25 bg-surface p-5 sm:p-6">
              <h3 className="text-base font-semibold text-ink">同名素材</h3>
              <div className="mt-3 divide-y divide-line/20">
                {diagnostics.duplicate_filenames.map((item) => (
                  <div key={item.filename} className="flex items-center justify-between gap-3 py-2 text-sm first:pt-0 last:pb-0">
                    <span className="truncate text-ink">{item.filename}</span>
                    <span className="shrink-0 font-semibold text-muted">{item.count} 条</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {diagnostics && (
            <details className="rounded-lg border border-line/25 bg-surface">
              <summary className="cursor-pointer px-5 py-4 text-sm font-semibold text-ink hover:bg-paper/60 focus-visible:ring-2 focus-visible:ring-primary/30 sm:px-6">路径与旧数据根</summary>
              <div className="space-y-5 border-t border-line/20 p-5 sm:p-6">
                <div className="space-y-2">
                  {[
                    ["数据目录", diagnostics.data_root],
                    ["数据库", diagnostics.db_path],
                    ["素材目录", diagnostics.jobs_dir],
                    ["日志目录", diagnostics.logs_dir],
                  ].map(([label, value]) => (
                    <div key={label} className="grid gap-1 text-sm sm:grid-cols-[5rem_1fr]">
                      <span className="font-semibold text-muted">{label}</span>
                      <span className="break-all font-mono text-ink/80">{value}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-ink">已知数据根</h4>
                  <div className="mt-2 divide-y divide-line/20">
                    {diagnostics.legacy_roots.map((root) => (
                      <div key={root.path} className="flex flex-col gap-1 py-2 text-sm sm:flex-row sm:items-center sm:justify-between">
                        <span className="min-w-0 break-all font-mono text-ink/75">{root.path}</span>
                        <span className="shrink-0 text-muted">{root.db_exists ? "有 DB" : "无 DB"} · {root.jobs_dir_exists ? "有 jobs" : "无 jobs"}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </details>
          )}
        </section>
      )}

      {(activeTab !== "data" || hasUnsavedChanges) && (
        <div className="sticky bottom-4 z-30 mt-5 flex flex-col gap-3 rounded-lg border border-line/30 bg-surface/95 p-3 shadow-lg backdrop-blur-md sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className={`text-sm font-semibold ${hasUnsavedChanges ? "text-primary" : "text-muted"}`}>
              {hasUnsavedChanges ? "有未保存更改" : saveSuccess || "当前配置已保存"}
            </p>
            {saveError && <p className="mt-0.5 truncate text-xs font-medium text-clay">{saveError}</p>}
          </div>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={!hasUnsavedChanges || saving}
            className="min-h-10 rounded-md bg-primary px-5 text-sm font-semibold text-white hover:bg-primary/90 disabled:cursor-not-allowed disabled:bg-line disabled:text-muted focus-visible:ring-2 focus-visible:ring-primary/40"
          >
            {saving ? "正在保存" : "保存更改"}
          </button>
        </div>
      )}
    </div>
  )
}
