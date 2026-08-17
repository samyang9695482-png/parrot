# 🦜 鹦鹉全球金融早报 PWA

> 每日8点，看懂全球金融。大字版、适老化、干净沉稳的全球金融早报。

把路透社、BBC、FT 等国外财经新闻，通过 DeepSeek AI 自动「分类 + 中译 + 压缩」，存入 Supabase，PWA 大字展示，底部固定夸克网盘下载按钮做拉新变现。

---

## 📁 项目结构

```
parrot/
├── frontend/                  # 纯 HTML/CSS/JS 前端（PWA）
│   ├── index.html             # 主页面（四个栏目纵向滚动）
│   ├── style.css              # 适老化大字版样式
│   ├── manifest.json          # PWA 清单
│   └── service-worker.js      # 离线缓存（保留最近 3 天）
├── backend/                   # Python 数据管线
│   ├── fetch_news.py          # RSS → AI → Supabase 主脚本
│   ├── requirements.txt       # pip 依赖
│   └── .env.example           # 环境变量模板（复制为 .env 使用）
├── .github/workflows/
│   └── daily_news.yml         # 每天 06:00（北京时间）自动抓取
└── README.md                  # 本文件
```

---

## ✅ 快速开始（3 步上线）

### 第一步：创建 Supabase 数据库

1. 去 [supabase.com](https://supabase.com) 注册免费层项目
2. 进入 **SQL Editor**，新建 Query，粘贴以下 SQL 并执行：

```sql
CREATE TABLE IF NOT EXISTS news (
    id           UUID PRIMARY KEY,
    date         DATE NOT NULL,
    category     VARCHAR(32) NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    source       VARCHAR(128),
    original_link TEXT,
    content_hash VARCHAR(64) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_date     ON news(date);
CREATE INDEX IF NOT EXISTS idx_news_category ON news(category);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_date_hash ON news(date, content_hash);

-- 开启 RLS（行级安全），匿名用户只可读，不可写入
ALTER TABLE news ENABLE ROW LEVEL SECURITY;

CREATE POLICY "任何人都可读新闻"
  ON news FOR SELECT
  USING (true);
```

> RLS 说明：开启后即使前端暴露 anon key，也 **只能 SELECT，不能写/改/删**，安全。

3. 在 **Settings → API** 页面拿到三个信息，稍后要用：
   - `Project URL`
   - `anon public key`（前端用）
   - `service_role key`（后端/GitHub Actions 用，⚠️ 不要泄露）

---

### 第二步：申请 DeepSeek API Key

1. 访问 [https://platform.deepseek.com](https://platform.deepseek.com) 注册
2. 进入 API Keys 页面创建一个 Key
3. （可选）充值几块钱，每天抓取大约只花几分钱

---

### 第三步：本地调试 + 部署

#### 3.1 先在本地跑通后端

```bash
cd backend

# 1. 准备环境变量
cp .env.example .env
# 编辑 .env 填入 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / DEEPSEEK_API_KEY

# 2. 安装依赖
pip install -r requirements.txt

# 3. 执行抓取（会先建表检测，没建表会提示你 SQL）
python fetch_news.py
```

成功执行后，去 Supabase Table Editor 看 `news` 表应该已经有数据了。

#### 3.2 改前端的 Supabase 信息

打开 `frontend/index.html`，修改顶部两处：

```js
const SUPABASE_URL = 'https://你的项目.supabase.co';
const SUPABASE_ANON_KEY = '你的 anon public key';
```

同时把底部下载按钮的链接改成你自己的夸克网盘分享链接：

```html
<a id="downloadBtn" href="https://pan.quark.cn/s/你的分享ID" ...>
```

#### 3.3 本地验证前端

前端是纯静态文件，用任何静态服务器都行，推荐 `npx serve`：

```bash
cd frontend
npx serve .
# 浏览器打开 http://localhost:3000 查看效果
```

---

### 第四步：部署到 Vercel + GitHub Actions

#### 4.1 把代码推到 GitHub 仓库

```bash
git init
git add .
git commit -m "feat: 鹦鹉早报初版"
git branch -M main
git remote add origin https://github.com/你的用户名/parrot.git
git push -u origin main
```

#### 4.2 在 GitHub 配置 Secrets（给 Actions 用）

打开仓库 → **Settings → Secrets and variables → Actions → New repository secret**，添加 3 个 Secret：

| Secret 名                     | 填入内容                                       |
|------------------------------|-----------------------------------------------|
| `SUPABASE_URL`               | 你的 Supabase Project URL                      |
| `SUPABASE_SERVICE_ROLE_KEY`  | 你的 Supabase service_role key                 |
| `DEEPSEEK_API_KEY`           | 你的 DeepSeek API Key                          |

（可选，在 **Variables** 页而不是 Secrets 配置 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`，不填就用默认值）

之后可以手动点一次：仓库 **Actions → Daily News Fetch → Run workflow**，验证没问题。

#### 4.3 Vercel 托管前端

1. 去 [vercel.com](https://vercel.com) 用 GitHub 登录
2. **New Project** → 导入刚推的 parrot 仓库
3. **Framework Preset 选 Other**，**Root Directory 填 `frontend`**
4. 点 Deploy，几十秒后就有一个 `xxx.vercel.app` 域名了
5. （可选）Vercel 项目 Settings → Domains 绑你自己的域名

> 注意：Vercel 必须把 Root Directory 设为 `frontend`，否则它会把根目录当网站根，访问不到 index.html。

---

## 🎨 视觉规范备忘

| 项           | 值                            |
|-------------|-------------------------------|
| 主色          | `#1a3a5c`（深蓝）               |
| 辅助色（按钮）   | `#e8850e`（暖橙）               |
| 页面背景       | `#f5f6fa`（浅灰）               |
| 卡片背景       | `#ffffff` + 圆角 16px + 轻阴影 |
| 正文          | 22px / 行高 1.8                |
| 新闻标题       | 26px 加粗                      |
| 品牌大标题      | 32px 加粗                      |
| 栏目显示条数    | 每个栏目 2 条                 |
| 对比度         | ≥ 4.5:1（WCAG AA 适老化）     |

---

## 🔧 常见修改

### Q: 想改每个栏目显示几条？
编辑 `frontend/index.html` 顶部的：
```js
const NEWS_PER_CATEGORY = 2;  // 改成你想要的数量
```

### Q: 想加/删 RSS 源？
编辑 `backend/fetch_news.py` 顶部的 `RSS_FEEDS` 列表。默认已带 Reuters、BBC、FT、CNBC、CoinDesk。

### Q: 想改定时时间？
编辑 `.github/workflows/daily_news.yml` 的 cron：
- 北京时间 6:00 → `cron: "0 22 * * *"`（UTC 22:00 前一天）
- 北京时间 7:00 → `cron: "0 23 * * *"`
- 北京时间 8:00 → `cron: "0 0 * * *"`（UTC 0 点 = 北京 8 点当天）

### Q: 想换用别的 AI 模型？
修改 `.env` 里的 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`。因为用的是 OpenAI 兼容 SDK，任何兼容 Chat Completions 接口的服务都可以直接用（比如硅基流动、Groq、月之暗面等）。

### Q: 夸克网盘拉新链接在哪里弄？
1. 登录夸克网盘 PC 端或 App
2. 上传一份打包好的「完整数据包」（比如每日新闻 PDF/Excel 合集）
3. 点击「分享」→「生成链接」
4. 把拿到的 `https://pan.quark.cn/s/xxxxxx` 填到 `index.html` 底部按钮的 `href` 里
5. 去夸克网盘「拉新活动」页绑定提现方式，新用户注册/转存你就能拿到收益

---

## 🛡️ 安全注意事项

1. ⚠️ **SERVICE_ROLE_KEY 绝不能出现在前端仓库**，只能在：本机 `.env`、GitHub Secrets、服务器环境变量。
2. Supabase 一定要执行上面的 `ALTER TABLE news ENABLE ROW LEVEL SECURITY` + SELECT Policy，不然 anon key 能直接写表。
3. `.env` 文件一定不要 `git add`，已在默认忽略列表中。

---

## 🧪 完整测试清单

- [ ] 本地运行 `python fetch_news.py` 能成功写库
- [ ] `npx serve frontend` 打开页面能看到四个栏目
- [ ] 断网刷新页面仍能看到最近 3 天内容（PWA 离线生效）
- [ ] 手机浏览器打开 → 添加到主屏 → 点开是全屏 App 体验
- [ ] 点底部下载按钮能跳转到你的夸克网盘链接
- [ ] GitHub Actions 手动 Run workflow 成功，日志里显示「今日新增汇总」

---

🦜 祝你早日变现，财源滚滚！
