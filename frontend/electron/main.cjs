const { app, BrowserWindow, Menu, shell } = require("electron")
const { spawn } = require("child_process")
const crypto = require("crypto")
const fs = require("fs")
const http = require("http")
const net = require("net")
const path = require("path")

let mainWindow = null
let backendProcess = null
let backendLogPath = ""
let backendLogStream = null
let backendPort = parsePort(getEnv("FRAMEBENCH_BACKEND_PORT", "FILM_MASTER_BACKEND_PORT"), 0)
let apiToken = getEnv("FRAMEBENCH_LOCAL_TOKEN", "FILM_MASTER_LOCAL_TOKEN") || crypto.randomBytes(32).toString("hex")
const backendStartupTimeoutMs = parsePositiveInteger(getEnv("FRAMEBENCH_BACKEND_TIMEOUT_MS", "FILM_MASTER_BACKEND_TIMEOUT_MS"), 120000)

const isDev = process.env.ELECTRON_DEV === "1"
const backendBinaryName = process.platform === "win32" ? "framebench-backend.exe" : "framebench-backend"
const ffmpegBinaryName = process.platform === "win32" ? "ffmpeg.exe" : "ffmpeg"
const ffprobeBinaryName = process.platform === "win32" ? "ffprobe.exe" : "ffprobe"
const hasSingleInstanceLock = app.requestSingleInstanceLock()

if (!hasSingleInstanceLock) {
  app.quit()
}

function getEnv(primaryName, legacyName) {
  return process.env[primaryName] || process.env[legacyName] || ""
}

function parsePositiveInteger(value, fallback) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function parsePort(value, fallback) {
  const parsed = parsePositiveInteger(value, fallback)
  return parsed >= 0 && parsed <= 65535 ? parsed : fallback
}

function getAvailablePort(preferredPort) {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once("error", (err) => {
      if (err.code === "EADDRINUSE") {
        getAvailablePort(preferredPort + 1).then(resolve, reject)
      } else {
        reject(err)
      }
    })
    server.once("listening", () => {
      const address = server.address()
      const port = typeof address === "object" && address ? address.port : preferredPort
      server.close(() => resolve(port))
    })
    server.listen(preferredPort, "127.0.0.1")
  })
}

function dataRootHasDb(root) {
  return fs.existsSync(path.join(root, "film_master.db")) || fs.existsSync(path.join(root, "data", "film_master.db"))
}

function getPersistentDataRoot() {
  const configured = getEnv("FRAMEBENCH_DATA_DIR", "FILM_MASTER_DATA_DIR")
  if (configured) return configured

  const appSupport = app.getPath("appData")
  const candidates = [
    path.join(appSupport, "film-master"),
    path.join(appSupport, "拉片工作台"),
    path.join(appSupport, "Framebench"),
    app.getPath("userData"),
  ]

  return candidates.find(dataRootHasDb) || app.getPath("userData")
}

function openBackendLog(dataRoot) {
  try {
    const logsDir = path.join(dataRoot, "logs")
    fs.mkdirSync(logsDir, { recursive: true })
    backendLogPath = path.join(logsDir, "backend.log")
    backendLogStream = fs.createWriteStream(backendLogPath, { flags: "a" })
    writeBackendLog(`Framebench ${app.getVersion()} starting backend`)
  } catch (err) {
    backendLogPath = ""
    backendLogStream = null
    console.error("[backend] failed to open log file:", err.message)
  }
}

function writeBackendLog(message) {
  if (!backendLogStream) return
  backendLogStream.write(`[${new Date().toISOString()}] ${message}\n`)
}

function waitForBackend(port, token, timeoutMs = 120000) {
  const startedAt = Date.now()

  return new Promise((resolve, reject) => {
    const probe = () => {
      let settled = false
      const failOnce = () => {
        if (settled) return
        settled = true
        retry()
      }
      const req = http.get({
        host: "127.0.0.1",
        port,
        path: "/api/health",
        timeout: 1000,
        headers: { "X-Framebench-Token": token },
      }, (res) => {
        res.resume()
        if (res.statusCode === 200) {
          settled = true
          resolve()
          return
        }
        failOnce()
      })

      req.on("timeout", () => {
        failOnce()
        req.destroy()
      })
      req.on("error", failOnce)
    }

    const retry = () => {
      if (Date.now() - startedAt > timeoutMs) {
        reject(new Error(`后端启动超时，端口 ${port} 在 ${Math.round(timeoutMs / 1000)} 秒内未通过健康检查`))
      } else {
        setTimeout(probe, 500)
      }
    }

    probe()
  })
}

async function startBackend() {
  backendPort = await getAvailablePort(backendPort)
  const dataRoot = getPersistentDataRoot()
  openBackendLog(dataRoot)

  const baseEnv = {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    FRAMEBENCH_BACKEND_HOST: "127.0.0.1",
    FRAMEBENCH_BACKEND_PORT: String(backendPort),
    FRAMEBENCH_DATA_DIR: dataRoot,
    FRAMEBENCH_BACKEND_LOG_FILE: backendLogPath,
    FRAMEBENCH_LOCAL_TOKEN: apiToken,
    FILM_MASTER_BACKEND_HOST: "127.0.0.1",
    FILM_MASTER_BACKEND_PORT: String(backendPort),
    FILM_MASTER_DATA_DIR: dataRoot,
    FILM_MASTER_BACKEND_LOG_FILE: backendLogPath,
    FILM_MASTER_LOCAL_TOKEN: apiToken,
  }

  let command = "python3"
  let args = ["-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", String(backendPort)]
  let cwd = path.join(__dirname, "..", "..")
  let env = baseEnv

  if (!isDev) {
    const runtimeDir = path.join(process.resourcesPath, "backend-runtime")
    const bundledBackend = path.join(runtimeDir, "backend", backendBinaryName)
    if (!fs.existsSync(bundledBackend)) {
      throw new Error(`缺少打包后的后端 runtime: ${bundledBackend}。请先运行 npm run backend:runtime，然后重新打包。`)
    }

    const binDir = path.join(runtimeDir, "bin")
    const ffmpegPath = path.join(binDir, ffmpegBinaryName)
    const ffprobePath = path.join(binDir, ffprobeBinaryName)

    command = bundledBackend
    args = []
    cwd = runtimeDir
    env = {
      ...baseEnv,
      PATH: fs.existsSync(binDir) ? `${binDir}${path.delimiter}${process.env.PATH || ""}` : process.env.PATH || "",
      ...(fs.existsSync(ffmpegPath) ? { FRAMEBENCH_FFMPEG_BIN: ffmpegPath, FILM_MASTER_FFMPEG_BIN: ffmpegPath } : {}),
      ...(fs.existsSync(ffprobePath) ? { FRAMEBENCH_FFPROBE_BIN: ffprobePath, FILM_MASTER_FFPROBE_BIN: ffprobePath } : {}),
    }
  }

  backendProcess = spawn(command, args, {
    cwd,
    stdio: "pipe",
    env,
    detached: process.platform !== "win32",
  })

  backendProcess.stdout.on("data", (data) => {
    const text = data.toString().trim()
    console.log(`[backend] ${text}`)
    writeBackendLog(`[stdout] ${text}`)
  })
  backendProcess.stderr.on("data", (data) => {
    const text = data.toString().trim()
    console.error(`[backend] ${text}`)
    writeBackendLog(`[stderr] ${text}`)
  })
  backendProcess.on("error", (err) => {
    console.error("[backend] failed to start:", err.message)
    writeBackendLog(`[error] failed to start: ${err.message}`)
  })
  backendProcess.on("exit", (code, signal) => {
    writeBackendLog(`[exit] code=${code} signal=${signal}`)
    if (code !== 0 && signal !== "SIGTERM") {
      console.error(`[backend] exited unexpectedly: code=${code} signal=${signal}`)
    }
  })
}

function stopBackend() {
  if (backendProcess) {
    const processToStop = backendProcess
    backendProcess = null
    try {
      if (process.platform !== "win32" && processToStop.pid) {
        process.kill(-processToStop.pid, "SIGTERM")
      } else {
        processToStop.kill("SIGTERM")
      }
    } catch (err) {
      console.error("[backend] failed to stop process group:", err.message)
      try { processToStop.kill("SIGTERM") } catch { /* process already exited */ }
    }
  }
  if (backendLogStream) {
    backendLogStream.end()
    backendLogStream = null
  }
}

function createWindow(startupError = null, loading = false) {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: "Framebench / 拉片工作台",
    backgroundColor: "#F7F1E7",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  Menu.setApplicationMenu(null)

  // Open external links in default browser
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: "deny" }
  })

  if (startupError) {
    loadStartupError(startupError)
  } else if (loading) {
    loadStartupLoading()
  } else {
    loadAppContent()
  }
}

function loadAppContent() {
  if (!mainWindow) return

  if (isDev) {
    const url = new URL("http://127.0.0.1:5174")
    url.searchParams.set("api", `http://127.0.0.1:${backendPort}/api`)
    url.searchParams.set("token", apiToken)
    mainWindow.loadURL(url.toString())
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"), {
      query: {
        api: `http://127.0.0.1:${backendPort}/api`,
        token: apiToken,
      },
    })
  }
}

function loadStartupError(error) {
  if (!mainWindow) return
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(renderStartupError(error))}`)
}

function loadStartupLoading() {
  if (!mainWindow) return
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(renderStartupLoading())}`)
}

function renderStartupLoading() {
  return `<!doctype html>
<html lang="zh-CN">
  <meta charset="utf-8" />
  <title>Framebench 启动中</title>
  <body style="margin:0;background:#f7f1e7;color:#2f2722;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;">
    <main style="max-width:560px;padding:48px;">
      <p style="font-size:12px;letter-spacing:.24em;text-transform:uppercase;color:#9a8f86;">Framebench / 拉片工作台</p>
      <h1 style="font-size:28px;font-weight:500;margin:12px 0;">正在启动后端服务</h1>
      <p style="line-height:1.7;color:#6f655c;">首次启动或更新后可能需要一点时间，准备好后会自动进入工作台。</p>
    </main>
  </body>
</html>`
}

function renderStartupError(error) {
  const message = String(error && error.message ? error.message : error)
  const logHint = backendLogPath
    ? `<p style="margin-top:16px;font-size:12px;color:#9a8f86;">日志文件：${escapeHtml(backendLogPath)}</p>`
    : ""
  return `<!doctype html>
<html lang="zh-CN">
  <meta charset="utf-8" />
  <title>Framebench 启动失败</title>
  <body style="margin:0;background:#f7f1e7;color:#2f2722;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;">
    <main style="max-width:560px;padding:48px;">
      <p style="font-size:12px;letter-spacing:.24em;text-transform:uppercase;color:#9a8f86;">Framebench / 拉片工作台</p>
      <h1 style="font-size:28px;font-weight:500;margin:12px 0;">后端服务没有启动成功</h1>
      <p style="line-height:1.7;color:#6f655c;">${escapeHtml(message)}</p>
      ${logHint}
      <p style="margin-top:24px;font-size:12px;color:#9a8f86;">请检查 Python 依赖、端口占用，或重新启动应用。</p>
    </main>
  </body>
</html>`
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;")
}

app.whenReady().then(async () => {
  if (!hasSingleInstanceLock) return

  createWindow(null, true)

  try {
    await startBackend()
    await waitForBackend(backendPort, apiToken, backendStartupTimeoutMs)
    loadAppContent()
  } catch (err) {
    console.error("[backend] startup check failed:", err)
    loadStartupError(err)
  }

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on("second-instance", () => {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
})

app.on("window-all-closed", () => {
  stopBackend()
  app.quit()
})

app.on("before-quit", () => {
  stopBackend()
})
