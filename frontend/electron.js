const { app, BrowserWindow, shell, session } = require('electron')
const path = require('path')

const isDev = process.env.NODE_ENV !== 'production'
const DEV_URL = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173'

function createWindow() {
    const mainWindow = new BrowserWindow({
        width: 1500,
        height: 950,
        minWidth: 1100,
        minHeight: 700,
        webPreferences: {
            // The renderer only talks to the local backend over HTTP; it has
            // no need for Node, and leaving it off closes the obvious hole.
            nodeIntegration: false,
            contextIsolation: true,
        },
        titleBarStyle: 'hiddenInset',
        // Matches the HUD background so there's no white flash on launch.
        backgroundColor: '#050a14',
        show: false,
    })

    if (isDev) {
        mainWindow.loadURL(DEV_URL)
        mainWindow.webContents.openDevTools({ mode: 'detach' })
    } else {
        // HashRouter, so deep links resolve from file:// with no server.
        mainWindow.loadFile(path.join(__dirname, 'dist', 'index.html'))
    }

    mainWindow.once('ready-to-show', () => mainWindow.show())

    // External links open in the real browser, not inside the app shell.
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        shell.openExternal(url)
        return { action: 'deny' }
    })
}

app.whenReady().then(() => {
    // A page loaded from file:// sends "Origin: null", which the backend's CORS
    // rule (local origins only) refuses. Present the app as localhost instead.
    session.defaultSession.webRequest.onBeforeSendHeaders(
        { urls: ['http://127.0.0.1:*/*', 'http://localhost:*/*'] },
        (details, callback) => {
            if (details.requestHeaders.Origin === 'null') details.requestHeaders.Origin = 'http://localhost'
            callback({ requestHeaders: details.requestHeaders })
        },
    )

    // Voice needs the microphone. Grant it for the local app only, and refuse
    // everything else rather than prompting for permissions we never use.
    session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
        const url = webContents.getURL()
        const isLocal = url.startsWith('file://') || url.startsWith('http://localhost')
        callback(isLocal && (permission === 'media' || permission === 'audioCapture'))
    })

    createWindow()

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
})

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
})
