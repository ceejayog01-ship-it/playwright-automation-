> **Superseded.** This is the original demo-only README. The project now supports `ACCOUNT_MODE=real` as an explicit opt-in; see `../../RUNBOOK.md`.

# Headway MT5 Web Terminal Automation (Demo Only)

Playwright automation for `https://hw.online/webterminal/mt5-demo/`

**Demo accounts only. Never use on real money.**

---

## Important Reality Check (GitHub Actions + Cloudflare)

GitHub Actions runners use **datacenter IPs**.  
Cloudflare on `hw.online` is aggressive against datacenter traffic.

| Setup                                          | Chance of passing Cloudflare |
|------------------------------------------------|------------------------------|
| GitHub Actions + no proxy                      | Very low                     |
| GitHub Actions + good residential proxy        | Medium to Good               |
| Local PC + real profile + residential proxy    | Highest                      |

**A residential proxy is almost mandatory** if you want this to work reliably on GitHub Actions.

---

## 1. Local Testing (Recommended First)

```bash
npm install
npx playwright install chromium

cp .env.example .env
# Edit .env with your DEMO credentials + residential proxy
```

Useful local settings in `.env`:

```env
HEADLESS=false
USER_DATA_DIR=/home/youruser/headway-chrome-profile
PROXY_SERVER=http://user:pass@residential-proxy:port
```

Then run:

```bash
npm run login
```

---

## 2. Step-by-step: Deploy on GitHub Actions

### Step 1 — Push the project to GitHub

```bash
git init
git add .
git commit -m "Initial Headway MT5 demo automation"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### Step 2 — Add GitHub Secrets

Go to your repository → **Settings → Secrets and variables → Actions → New repository secret**

Add these four secrets:

| Secret Name          | Value                                      |
|----------------------|--------------------------------------------|
| `WEB_TERMINAL_URL`   | `https://hw.online/webterminal/mt5-demo/`  |
| `MT5_LOGIN`          | Your demo account number                   |
| `MT5_PASSWORD`       | Your demo trading password                 |
| `PROXY_SERVER`       | `http://user:pass@host:port` (residential) |

> Without a good residential proxy the workflow will most likely fail on the Cloudflare challenge.

### Step 3 — Run the workflow

1. Go to the **Actions** tab in your repository
2. Select **Headway MT5 Demo Automation**
3. Click **Run workflow** → **Run workflow**
4. Wait for it to finish
5. Download the artifact named `automation-artifacts-...` to see screenshots of success or failure

### Step 4 — (Optional) Scheduled runs

Edit `.github/workflows/demo-run.yml` and uncomment the `schedule` section.

---

## 3. Project Structure

```
headway-mt5-web-automation/
├── .github/workflows/demo-run.yml   ← GitHub Actions workflow
├── config/selectors.ts              ← All selectors in one place
├── src/
│   ├── browser.ts                   ← Stealth + proxy + profile support
│   ├── login.ts
│   ├── order.ts
│   ├── utils.ts
│   └── index.ts
├── .env.example
├── package.json
└── README.md
```

---

## 4. Current Capabilities

- Login with multi-strategy selectors
- Basic Buy / Sell market order click
- Stealth plugin
- Proxy support (critical for GitHub Actions)
- Screenshot on failure
- Demo safety guard (aborts on Real/Live server text)

---

## 5. Disclaimer

Automating a broker web terminal may violate the broker’s Terms of Service.  
This project is for educational and demo-account testing purposes only.  
You accept all risk.
