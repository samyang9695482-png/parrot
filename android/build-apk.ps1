# 鹦鹉全球金融早报 PWA → Android APK 构建指南
# Windows 环境，一行一行复制运行即可
# 本脚本按顺序执行：检查环境 → 安装 JDK/SDK → 安装 Bubblewrap → 初始化项目 → 生成 APK
#
# 推荐两种方案：
#   【方案 A：零环境 · 最快速 · 5 分钟】在线 PWA Builder（见下方 README.md A 方案说明）
#   【方案 B：本地构建 · 可发布到应用商店 · 1 小时准备】运行当前 .ps1 脚本

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $Root
Write-Host "🦜 鹦鹉早报 PWA → APK 构建脚本" -ForegroundColor Cyan
Write-Host "项目根目录: $ProjectRoot"
Write-Host ""

# ============================================================
# 1. 安装 Node.js 依赖：PWABuilder / Bubblewrap 社区最稳定
# ============================================================
Write-Host "📦 [1/7] 安装 @bubblewrap/cli（Google 官方 TWA 打包工具）..." -ForegroundColor Cyan
& npm i -g @bubblewrap/cli
if ($LASTEXITCODE -ne 0) {
  Write-Host "❌ npm 安装 bubblewrap 失败。请检查网络或换 npm 镜像：`npm config set registry https://registry.npmmirror.com`" -ForegroundColor Red
  exit 1
}
Write-Host "   bubblewrap 已安装。版本: " -NoNewline; & bubblewrap --version 2>&1

# ============================================================
# 2. 尝试自动安装 JDK 17 + Android SDK（本机已安装则跳过）
# ============================================================
Write-Host "☕ [2/7] 检查 Java JDK 17 ..." -ForegroundColor Cyan
$JavaOk = $false
try { $javaExe = Get-Command java -ErrorAction Stop; if (& $javaExe -version 2>&1 | Select-String 'version "17\.') { $JavaOk = $true } } catch {}
if (-not $JavaOk) {
  Write-Host "   ❌ 未发现 JDK 17。"
  Write-Host "   请先安装 Eclipse Temurin JDK 17（推荐）:  https://adoptium.net/  选择 JDK 17 / Windows x64 .msi 安装。安装时勾选「Set JAVA_HOME variable」和「Add to PATH」。安装完成后重新打开 PowerShell 再运行本脚本。"
  Write-Host "   或者手动下载：https://github.com/adoptium/temurin17-binaries/releases"
  exit 2
}
Write-Host "   ✅ JDK 17 就绪"

# ============================================================
# 3. 检查 ANDROID_HOME（Android SDK）
# ============================================================
Write-Host "🤖 [3/7] 检查 Android SDK ..." -ForegroundColor Cyan
$SdkHome = $env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, (Join-Path $env:LOCALAPPDATA 'Android\Sdk') | Where-Object { $_ -and (Test-Path (Join-Path $_ 'platforms')) -and (Test-Path (Join-Path $_ 'build-tools')) } | Select-Object -First 1
if (-not $SdkHome) {
  Write-Host "   ❌ 未发现 Android SDK。"
  Write-Host "   两种解决方式二选一："
  Write-Host "     ① 安装 Android Studio：https://developer.android.com/studio → 打开后，More Actions → SDK Manager → 勾选 Android 13 (Tiramisu) 或最新 → 勾选 Android SDK Build-Tools / Platform-Tools → Install。SDK 默认位置：$env:LOCALAPPDATA\Android\Sdk"
  Write-Host "     ② 只装命令行工具：下载 Windows command line tools from https://developer.android.com/studio#command-line-tools-only → 解压到 `$env:LOCALAPPDATA\Android\Sdk\cmdline-tools\latest\bin\sdkmanager.bat` → 运行："
  Write-Host "         sdkmanager --install `"platforms;android-33`" `"build-tools;33.0.2`" platform-tools"
  Write-Host "   安装完成后重新运行本脚本。"
  exit 3
}
Write-Host "   ✅ Android SDK 就绪: $SdkHome"

# ============================================================
# 4. 如果 keystore 不存在，生成一个（调试用）。正式发布请自行保管安全密码
# ============================================================
$KeystoreDir = Join-Path $Root 'keystore'
$KeystorePath = Join-Path $KeystoreDir 'android-keystore.jks'
$KeyAlias = 'yinwu'
if (-not (Test-Path $KeystorePath)) {
  Write-Host "🔑 [4/7] 生成安卓签名 keystore（调试用）..." -ForegroundColor Cyan
  $StorePass = 'yinwu2026debug'
  $KeyPass = 'yinwu2026debug'
  $cmd = "& `"$((Get-Command keycert -ErrorAction SilentlyContinue).Source ?? (Join-Path (Split-Path -Parent (Split-Path -Parent ((Get-Command java).Source))) 'bin\keytool.exe'))`"
  $keytool = Join-Path (Split-Path -Parent (Split-Path -Parent ((Get-Command java).Source))) 'bin\keytool.exe'
  if (-not (Test-Path $keytool)) {
    Write-Host "   ⚠️  无法找到 keytool.exe，跳过 keystore 生成。你可以在安装 JDK 后手动生成："
    Write-Host "      keytool -genkey -v -keystore `"$KeystorePath`" -alias $KeyAlias -keyalg RSA -keysize 2048 -validity 10000 -storepass yinwu2026debug -keypass yinwu2026debug -dname `"CN=YinwuMorningNews, OU=Fintech, O=Yinwu, L=Beijing, ST=Beijing, C=CN`""
  } else {
    & $keytool -genkey -noprompt -v `
      -keystore $KeystorePath `
      -alias $KeyAlias `
      -keyalg RSA -keysize 2048 -validity 10000 `
      -storepass $StorePass -keypass $KeyPass `
      -dname "CN=YinwuMorningNews, OU=Fintech, O=Yinwu, L=Beijing, ST=Beijing, C=CN" 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Warning "keystore 生成失败，跳过" } else {
      # 生成 keystore 信息 JSON（bubblewrap build 时会读取）
      $kiobj = @{
        password = $StorePass
        alias = $KeyAlias
        keypassword = $KeyPass
        keystore = $KeystorePath
      } | ConvertTo-Json -Depth 5
      Set-Content -Path (Join-Path $KeystoreDir 'keystore-info.json') -Value $kiobj -Encoding UTF8
      Write-Host "   ✅ keystore 已生成（调试密码：yinwu2026debug，别用于商店）"
    }
  }
} else {
  Write-Host "🔑 [4/7] 跳过 keystore（已存在）" -ForegroundColor Cyan
}

# ============================================================
# 5. bubblewrap init（如果 android/ 下已经有它创建的子目录则跳过）
# ============================================================
$InitMarker = Join-Path $Root 'app/build.gradle'
if (-not (Test-Path $InitMarker)) {
  Write-Host "🧩 [5/7] bubblewrap init：基于现有 manifest 初始化安卓项目..." -ForegroundColor Cyan
  Push-Location $Root
  try {
    # 用 --insecure 忽略 https 证书自签，manifest 是已知安全的线上地址
    & bubblewrap init --manifest='https://peaceful-panda-50f9c5.netlify.app/manifest.json' --directory='.' 2>&1
  }
  finally { Pop-Location }
  Write-Host "   ✅ 项目初始化完成。安卓源代码目录已在 $Root"
} else {
  Write-Host "🧩 [5/7] 跳过 bubblewrap init（build.gradle 已存在）" -ForegroundColor Cyan
}

# ============================================================
# 6. 覆盖 bubblewrap 配置，确保：不申请权限、中文名称、图标用 yinwu.png
# ============================================================
Write-Host "🛠  [6/7] 把 twa-manifest.json 的要求注入到安卓 manifest + 替换启动图标..." -ForegroundColor Cyan
$AppManifest = Join-Path $Root 'app/src/main/AndroidManifest.xml'
if (Test-Path $AppManifest) {
  Write-Host "   当前 AndroidManifest.xml 位置：$AppManifest"
  # bubblewrap init 生成的 AndroidManifest 通常会带 <uses-permission> INTERNET 和其他，这里用 PowerShell 正则确保只有 INTERNET（TWA 必须的，其它删除）
  $orig = Get-Content $AppManifest -Raw
  # 先剔除所有 uses-permission 行（<uses-permission .../> 或多行）
  $stripped = $orig -replace '(?ms)\s*<uses-permission\b[^>]*\/>', "`n"
  # 再在 <manifest ...> tag 闭合处后加入仅 INTERNET（TWA 唯一必须权限）
  if ($stripped -notmatch 'INTERNET') {
    $stripped = $stripped -replace '(?<xmlns>.*?)(?<endmanifesttag>package\s*=\s*"[^"]+"[^>]*)(?=>)', { "NOTUSED" }  # placeholder
    # 更简单：在第一个 <application> 之前插入
    $stripped = $stripped -replace '(?=\s*<application\b)', "`n    <uses-permission android:name=`"android.permission.INTERNET`"/>`n"
    Set-Content -Path $AppManifest -Value $stripped -Encoding UTF8
    Write-Host "   ✅ 权限已最小化：仅保留 INTERNET（TWA 必需），其它全删除"
  }
  # 替换 launcher_name 字符串（简体中文名称）
  $Strings = Join-Path $Root 'app/src/main/res/values/strings.xml'
  if (Test-Path $Strings) {
    $s = Get-Content $Strings -Raw
    $s = $s -replace '(?<=<string name="launcherName">)[^<]*?(?=</string>)', '鹦鹉全球金融早报'
    Set-Content -Path $Strings -Value $s -Encoding UTF8
    Write-Host "   ✅ strings.xml 应用名称已改为「鹦鹉全球金融早报」"
  }
} else {
  Write-Host "   ⚠️  尚未找到 AndroidManifest.xml（bubblewrap init 可能未完全成功），第 6 步稍后手动执行"
}
Write-Host "   💡 图标替换：首次运行 bubblewrap build 时会自动从 netlify.app 的 img/yinwu.png 拉取并生成所有 mipmap 尺寸；如果失败，将 yinwu.png 分别命名为 512.png/192.png 放到 android/app/src/main/res/mipmap-xxxhdpi/ 等对应目录。"

# ============================================================
# 7. bubblewrap build → 生成 APK
# ============================================================
Write-Host "🏗  [7/7] bubblewrap build → 生成 APK 安装包..." -ForegroundColor Cyan
Push-Location $Root
try {
  & bubblewrap build --skipPwaValidation 2>&1
} finally { Pop-Location }

# ============================================================
# 完成：查找生成的 APK 文件并显示路径
# ============================================================
Write-Host ""
Write-Host "🔎 查找 APK..." -ForegroundColor Cyan
$Apks = Get-ChildItem -Path $Root -Recurse -Filter '*.apk' -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -notmatch 'unaligned|unsigned' } |
  Sort-Object LastWriteTime -Descending
if ($Apks.Count -gt 0) {
  Write-Host "✅ 已生成 APK：" -ForegroundColor Green
  $Apks | ForEach-Object { Write-Host "    $($_.FullName)    (大小: $([math]::Round($_.Length/1MB,1)) MB, 时间: $($_.LastWriteTime.ToString('yyyy-MM-dd HH:mm')))" }
  Write-Host ""
  Write-Host "📱 安装方式：" -ForegroundColor Green
  Write-Host "   ① USB 连接安卓手机，开启 USB 调试，运行 `adb install `"$($Apks[0].FullName)`""
  Write-Host "   ② 或把 APK 发到手机微信/QQ，直接点击文件安装（需要开启「允许安装未知来源应用」）"
} else {
  Write-Host "⚠️  build 结束但未找到 .apk 文件，请查看上方 bubblewrap build 的错误日志，按提示修复后重新运行第 7 步。" -ForegroundColor Yellow
}
