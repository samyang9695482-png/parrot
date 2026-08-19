# 🦜 鹦鹉全球金融早报 - PWA → Android APK 打包方案

> 两个打包方案，二选一。**推荐先试 方案 A（在线 PWA Builder）**，零环境，5 分钟出 APK。
> 需要发华为/小米/OPPO/Google Play 应用商店时用 **方案 B（本地 Bubblewrap CLI）**，全程可控。

---

## 📱 核心配置（两方案通用，本仓库已经改好了）

- **应用名称**：`鹦鹉全球金融早报`（桌面启动器名称）
- **应用图标**：`frontend/img/yinwu.png`（自动缩放到 192/512/mipmap 各尺寸）
- **启动页面 URL**：`https://peaceful-panda-50f9c5.netlify.app/index.html`
- **Package ID**：`cn.yinwu.finance.morning.paper`（Android 内部包名，不能改，改了要重新生成 assetlinks）
- **权限**：仅保留 `INTERNET`（TWA 必须项）。不申请 **通讯录 / 相机 / 位置 / 存储 / 麦克风 / 短信** 任何隐私权限。Android 安装后「权限」页显示 `无权限`。
- **显示模式**：`standalone`（全屏，无 Chrome 地址栏，像原生 App）

---

## 🅰️ 方案 A：在线 PWA Builder（推荐，零环境 · 5 分钟出 APK）

> 微软/PWABuilder 官方站点，不需要装 Java/Android SDK/Gradle。

### 操作步骤

1. 打开 → [https://www.pwabuilder.com/](https://www.pwabuilder.com/)
2. 首页输入网址：`https://peaceful-panda-50f9c5.netlify.app/` → 点 **Start**
3. 右上角会出现一个总分（0~200），如果你已把 `frontend/img/yinwu.png` 推到 Netlify，**Icons 项要 3 项都打勾**，然后点 **Package for Stores**
4. 在 **Android** 卡片上点 **Generate**
5. 生成完成后，点绿色下载按钮，下载两个关键文件：
   - `app-release-signed.apk`（**这就是你要的安装包**，直接装手机就能用）
   - `*.aab`（Google Play 商店发布用，留着即可）
6. 把 `app-release-signed.apk` 重命名为 `鹦鹉全球金融早报_v1.0.0.apk`，发给手机安装

### 方案 A 检查清单（影响打包结果）

- [ ] `frontend/img/yinwu.png` **必须存在**，且是 512×512 以上 PNG（图标小了 PWABuilder 会给黄色警告但仍能打包，图标会模糊）
- [ ] `frontend/manifest.json` 的 `icons` 三项 `src: img/yinwu.png` 已经在本仓库改好（见本次改动）
- [ ] 站点必须 **HTTPS**（Netlify 自动满足）
- [ ] 站点必须有 **Service Worker**（本仓库有）

---

## 🅱️ 方案 B：本地 Bubblewrap CLI（Google 官方 TWA，可发应用商店）

本仓库在 `parrot/android/` 下准备了完整脚手架和一键构建脚本。

### 环境要求（一次安装，终身可用）

| 工具 | 版本 | 下载地址 |
|------|------|---------|
| Node.js | ≥ 18 ✅（你已有 24.11.1）| 已装 |
| JDK | 17 LTS | https://adoptium.net/ → 选 Temurin 17 / Windows x64 .msi；安装时**勾选** Set JAVA_HOME + Add to PATH |
| Android SDK | Build-Tools 33+ & Platform 33+ | 方式一：装 Android Studio → More Actions → SDK Manager；方式二：装 commandlinetools-win → 运行 `sdkmanager --install "platforms;android-33" "build-tools;33.0.2" platform-tools` |

### 运行脚本（Windows PowerShell）

```powershell
# 进到 parrot/android/ 目录，一键执行
cd E:\鹦鹉\parrot\android
powershell -ExecutionPolicy Bypass -File .\build-apk.ps1
```

脚本会自动完成 7 步：
1. `npm i -g @bubblewrap/cli`（装 TWA 打包工具）
2. 检查 JDK 17 ✅
3. 检查 Android SDK ✅
4. 生成签名 keystore（`android/keystore/android-keystore.jks`，调试密码固定为 `yinwu2026debug`）
5. `bubblewrap init --manifest=线上地址`（生成完整安卓 Gradle 项目）
6. 注入最小权限 + 中文名称 + 图标
7. `bubblewrap build` → 输出 APK

### 生成的 APK 在哪？

脚本运行结束会自动打印，通常在：

```
E:\鹦鹉\parrot\android\app\release\app-release-signed.apk
```

### 安装到手机

```powershell
adb install "E:\鹦鹉\parrot\android\app\release\app-release-signed.apk"
```

或把 APK 发到手机微信/QQ，直接点击文件安装。

---

## 🚦 构建后必做：让 APK 全屏显示（去掉顶部 Chrome 灰色地址栏）

TWA（Trusted Web Activity）要求域名所有权校验，否则会有地址栏。做法：**把仓库里的 `frontend/.well-known/assetlinks.json` 推到 Netlify**，同时把你 APK 签名的 SHA256 指纹填进去。

### Step 1：获取 APK 签名的 SHA256 指纹

```powershell
keytool -list -v -keystore E:\鹦鹉\parrot\android\keystore\android-keystore.jks -alias yinwu -storepass yinwu2026debug
```

在输出中找到 `SHA256:` 那一行，格式类似：

```
SHA256: AB:CD:EF:12:34:56:...:90
```

把整串（冒号分隔的 64 hex 字节）全部复制。

### Step 2：替换 `frontend/.well-known/assetlinks.json`

用拿到的 SHA256 替换文件里的 `TODO:` 占位行，最后长这样：

```json
[
  {
    "relation": ["delegate_permission/common.handle_all_urls"],
    "target": {
      "namespace": "android_app",
      "package_name": "cn.yinwu.finance.morning.paper",
      "sha256_cert_fingerprints": [
        "AB:CD:EF:12:34:56:78:90:AB:CD:EF:12:34:56:78:90:AB:CD:EF:12:34:56:78:90:AB:CD:EF:12:34:56:78:90"
      ]
    }
  }
]
```

### Step 3：推到 Netlify

把 `frontend/.well-known/assetlinks.json` 推到 Netlify 后，验证：

```
https://peaceful-panda-50f9c5.netlify.app/.well-known/assetlinks.json
```

用 Google 官方校验器打开：
```
https://developers.google.com/digital-asset-links/tools/generator
```
如果显示「✓ Verified」就完成。重新装 APK 后打开就是全屏，没有 Chrome 地址栏。

---

## 🔒 关于签名密码的说明

一键脚本默认用的调试密码：`yinwu2026debug` / alias `yinwu`。

> ⚠️ **正式发应用商店请立即换密码**：
> 1. 运行 `keytool -importkeystore -srckeystore android/keystore/android-keystore.jks -destkeystore android/keystore/android-keystore.jks -deststoretype pkcs12`
> 2. 运行 `keytool -keypasswd -alias yinwu -keystore android/keystore/android-keystore.jks`
> 3. 把新密码写入 `android/keystore/keystore-info.json` 对应字段。
> 4. **把 `android/keystore/` 目录加入 `.gitignore` 并确保不被 commit**（如果要存到仓库里，先加密 zip 备份到别处）。

本仓库的 `.gitignore` 已经应该忽略 `*.jks`、`keystore-info.json` 和 `play-service-account.json`。如果没有，加这三行：

```
android/keystore/*.jks
android/keystore/*.json
!android/keystore/.gitkeep
```

---

## ❓ 常见问题

### Q1：为什么 APK 打开后最顶上有一条灰色 Chrome 地址栏？
没有完成 `.well-known/assetlinks.json` 的 SHA256 指纹匹配。按上面「构建后必做」一节操作。

### Q2：安装 APK 时提示「应用未安装」？
- 手机上装了旧版同名 APK，签名不一样 → 先卸载旧版。
- APK 签名或 zip 对齐损坏 → 重新运行 `build-apk.ps1` 第 7 步。

### Q3：APK 打开后是空白页？
- 先在手机 Chrome 里打开 `https://peaceful-panda-50f9c5.netlify.app/`，确认 PWA 本身能加载正常。
- 如果 Chrome 正常但 TWA 空白，检查 `assetlinks.json` 的 package_name 和 SHA256 是否与实际 APK 一致。

### Q4：能不能用 Capacitor/Cordova？
可以，但会变成原生 webview 加载本地 HTML/CSS（不再联网），需要把整个 frontend/ 目录打包进 APK，体积会膨胀 3-5 倍，还要自己做「从 Supabase 拉当日新闻」的离线/在线切换。TWA 是最轻的方案，**100% 复用现有的 PWA 功能**，强烈建议用 TWA。

### Q5：怎么最小化权限？
方案 A 的 PWABuilder 默认不加多余权限。方案 B 脚本第 6 步会删所有 `<uses-permission>`，只保留 `INTERNET`，打开 APK 的「应用信息 → 权限」页会显示「此应用不请求任何权限」。

---

## 📁 本仓库新增文件一览

```
parrot/android/
├── build-apk.ps1              ← 一键构建脚本（Windows PowerShell）
├── twa-manifest.json          ← Bubblewrap 打包配置（包名/名称/图标/权限）
├── fastlane/                  ← （可选，以后发商店接 fastlane 自动截图）
└── keystore/
    └── (运行脚本后生成：android-keystore.jks, keystore-info.json)

parrot/frontend/.well-known/
└── assetlinks.json            ← TWA 资产证明（需填 APK SHA256），推到 Netlify 后生效
```
