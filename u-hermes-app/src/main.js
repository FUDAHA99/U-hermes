const { app, BrowserWindow, Menu, Tray, shell, dialog, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const net = require('net');

// ── Constants ──
const APP_NAME = 'U-Hermes';
const DEFAULT_PORT = 9119;
const MAX_PORT = 9129;
const HERMES_STARTUP_TIMEOUT = 30000;

// ── Paths ──
const isDev = process.argv.includes('--dev');
const resourcesPath = isDev
  ? path.join(__dirname, '..', 'resources')
  : path.join(process.resourcesPath, 'resources');

// Python runtime location
function getPythonBin() {
  const platform = process.platform;
  const arch = process.arch;
  const pythonDir = path.join(resourcesPath, 'runtime', `python-${platform}-${arch}`);

  if (platform === 'win32') {
    const exe = path.join(pythonDir, 'python.exe');
    if (fs.existsSync(exe)) return exe;
  } else {
    const exe = path.join(pythonDir, 'bin', 'python3');
    if (fs.existsSync(exe)) return exe;
    const exe2 = path.join(pythonDir, 'bin', 'python');
    if (fs.existsSync(exe2)) return exe2;
  }
  return 'python3';
}

// Hermes venv python
function getHermesPython() {
  const hermesDir = path.join(resourcesPath, 'hermes');
  if (process.platform === 'win32') {
    return path.join(hermesDir, '.venv', 'Scripts', 'python.exe');
  }
  return path.join(hermesDir, '.venv', 'bin', 'python');
}

// User data directory
function getUserDataPath() {
  // Portable mode: check if portable/ exists next to app
  if (app.isPackaged) {
    const appDir = path.dirname(app.getPath('exe'));
    const portableData = path.join(appDir, 'data');
    if (fs.existsSync(portableData)) return portableData;
  }
  // Default: user's app data
  const dataDir = path.join(app.getPath('userData'), 'data');
  fs.mkdirSync(dataDir, { recursive: true });
  return dataDir;
}

// ── State ──
let mainWindow = null;
let tray = null;
let hermesProcess = null;
let dashboardPort = DEFAULT_PORT;
let isReady = false;

// ── Port Discovery ──
function findFreePort(start, end) {
  return new Promise((resolve) => {
    function tryPort(port) {
      if (port > end) { resolve(start); return; }
      const server = net.createServer();
      server.once('error', () => tryPort(port + 1));
      server.once('listening', () => {
        server.close(() => resolve(port));
      });
      server.listen(port, '127.0.0.1');
    }
    tryPort(start);
  });
}

// ── Config ──
function ensureConfig(dataDir) {
  fs.mkdirSync(path.join(dataDir, 'memory'), { recursive: true });
  fs.mkdirSync(path.join(dataDir, 'skills'), { recursive: true });
  fs.mkdirSync(path.join(dataDir, 'sessions'), { recursive: true });

  const configFile = path.join(dataDir, 'config.yaml');
  if (!fs.existsSync(configFile)) {
    const defaultConfig = `# U-Hermes Configuration
model:
  provider: ""
  model: ""

providers:
  deepseek:
    api_key: ""
    base_url: "https://api.deepseek.com/v1"

gateway:
  platforms: []

skills:
  extra_dirs: []

memory:
  enabled: true
`;
    fs.writeFileSync(configFile, defaultConfig, 'utf8');
  }
  return configFile;
}

function isModelConfigured(configFile) {
  try {
    const content = fs.readFileSync(configFile, 'utf8');
    return !content.includes('provider: ""');
  } catch { return false; }
}

// ── Hermes Dashboard Process ──
async function startHermes(dataDir, configFile) {
  const hermesPython = getHermesPython();
  if (!fs.existsSync(hermesPython)) {
    dialog.showErrorBox(APP_NAME,
      `未找到 Hermes Python:\n${hermesPython}\n\n请先运行安装程序。`);
    app.quit();
    return;
  }

  dashboardPort = await findFreePort(DEFAULT_PORT, MAX_PORT);

  const env = {
    ...process.env,
    HERMES_HOME: dataDir,
    HERMES_CONFIG: configFile,
    UV_INDEX_URL: 'https://pypi.tuna.tsinghua.edu.cn/simple',
    PIP_INDEX_URL: 'https://pypi.tuna.tsinghua.edu.cn/simple',
  };

  // Start Hermes Web UI dashboard
  hermesProcess = spawn(hermesPython, [
    '-m', 'hermes_cli.main', 'dashboard',
    '--port', String(dashboardPort),
    '--no-open',
    '--skip-build',
  ], {
    env,
    cwd: dataDir,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  hermesProcess.stdout.on('data', (data) => {
    const line = data.toString();
    console.log(`[hermes] ${line.trim()}`);
    if (line.includes('running') || line.includes('started') || line.includes('Uvicorn')) {
      isReady = true;
      if (mainWindow) {
        mainWindow.loadURL(`http://127.0.0.1:${dashboardPort}`);
      }
    }
  });

  hermesProcess.stderr.on('data', (data) => {
    console.error(`[hermes-err] ${data.toString().trim()}`);
  });

  hermesProcess.on('exit', (code) => {
    console.log(`[hermes] Process exited with code ${code}`);
    hermesProcess = null;
    isReady = false;
  });

  // Timeout: if not ready in 30s, show config page
  setTimeout(() => {
    if (!isReady && mainWindow) {
      const configHtml = path.join(resourcesPath, 'Config.html');
      if (fs.existsSync(configHtml)) {
        mainWindow.loadFile(configHtml);
      }
    }
  }, HERMES_STARTUP_TIMEOUT);
}

// ── Window ──
function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    title: APP_NAME,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
    show: false,
  });

  // Show loading page first
  mainWindow.loadFile(path.join(__dirname, 'loading.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ── App Lifecycle ──
app.whenReady().then(async () => {
  const dataDir = getUserDataPath();
  const configFile = ensureConfig(dataDir);

  createWindow();

  if (!isModelConfigured(configFile)) {
    // First run: show config page
    const configHtml = path.join(resourcesPath, 'Config.html');
    if (fs.existsSync(configHtml)) {
      mainWindow.loadFile(configHtml);
    }
  } else {
    // Start Hermes dashboard
    await startHermes(dataDir, configFile);
  }
});

app.on('window-all-closed', () => {
  if (hermesProcess) {
    hermesProcess.kill();
    hermesProcess = null;
  }
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  if (hermesProcess) {
    hermesProcess.kill();
    hermesProcess = null;
  }
});

app.on('activate', () => {
  if (!mainWindow) createWindow();
});
