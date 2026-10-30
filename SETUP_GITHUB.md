# MLB Stats Web App — GitHub Setup Guide

Follow these steps once and your dashboard will be available at a public URL
you can bookmark on your phone. It updates automatically every day at 10:15 AM.

---

## Step 1 — Create a free GitHub account
1. Go to **https://github.com** and click **Sign up**
2. Use any username (e.g. `alexjmartin100`) and your email
3. Verify your email when prompted

---

## Step 2 — Create a new repository
1. Once logged in, click the **+** button (top right) → **New repository**
2. Name it: `mlb-stats`
3. Set it to **Public** (required for free GitHub Pages)
4. Check **Add a README file**
5. Click **Create repository**

---

## Step 3 — Upload the files
1. In your new repo, click **Add file** → **Upload files**
2. Upload ALL of these files from your `MLB Daily Results` folder:
   - `fetch_mlb_stats.py`
   - `requirements.txt`
   - `manifest.json`
   - `sw.js`
   - `icon-192.png`
   - `icon-512.png`
3. Click **Commit changes**

Then upload the workflow file:
1. Click **Add file** → **Upload files** again
2. You need to create the folder structure: in the file name box type
   `.github/workflows/daily.yml` — GitHub will create the folders automatically
3. Paste the contents of `daily.yml` into the file editor
   (or drag it in — GitHub will place it correctly)
4. Click **Commit changes**

---

## Step 4 — Enable GitHub Pages
1. In your repo, click **Settings** (top tab)
2. In the left sidebar, click **Pages**
3. Under **Source**, select **Deploy from a branch**
4. Branch: **main**, Folder: **/ (root)**
5. Click **Save**

---

## Step 5 — Run it for the first time
1. Click the **Actions** tab in your repo
2. Click **MLB Stats Daily Update** in the left sidebar
3. Click **Run workflow** → **Run workflow** (green button)
4. Wait 3–5 minutes for it to finish

---

## Step 6 — Get your URL
Your dashboard is now live at:

```
https://YOUR-USERNAME.github.io/mlb-stats/mlb_daily_stats.html
```

Replace `YOUR-USERNAME` with your actual GitHub username.

**Bookmark this on your phone.** It updates automatically every day at 10:15 AM.

---

## How it works
- GitHub runs your Python script in the cloud every day at 10:15 AM
- It generates `mlb_daily_stats.html` with the prior day's stats
- GitHub Pages serves that file publicly at your URL
- You don't need your PC to be on — it all runs in GitHub's cloud
- The page also has a built-in auto-refresh timer so any open tabs update automatically

---

## Add to your phone's home screen

**iPhone (Safari):**
1. Open your URL in Safari
2. Tap the Share button (box with arrow) at the bottom
3. Tap **Add to Home Screen**
4. Name it "MLB Stats" → tap **Add**

**Android (Chrome):**
1. Open your URL in Chrome
2. Tap the 3-dot menu (top right)
3. Tap **Add to Home screen** (or Chrome may show an install banner automatically)
4. Tap **Add**

The app icon will appear on your home screen with the baseball logo and open full-screen like a native app.

---

## Optional — ESPN roster and standings secrets

The daily workflow runs `fetch_espn_rosters.py` before it builds the dashboard.
That script rewrites `espn_rosters.json` (rosters, and the team records /
category totals the standings tab reads). If ESPN refuses the request, times
out, or returns a bot challenge, the script keeps the previous
`espn_rosters.json` and the dashboard build continues.

League `2081322885` is private. An unauthenticated call to the reads API
returns HTTP 401 JSON (`AUTH_LEAGUE_NOT_VISIBLE`), so the refresh only
updates the file when these repository secrets are set
(**Settings → Secrets and variables → Actions**):

- `ESPN_S2` — the `espn_s2` cookie
- `ESPN_SWID` — the `SWID` cookie, including the curly braces

While logged in at `fantasy.espn.com`, open DevTools → Application → Cookies
and copy those two values. If either secret is missing, the workflow still
succeeds and the last committed snapshot is used.

`espn_bookmarklet.html` remains the manual fallback: run it in a logged-in
league tab and commit the download as `espn_rosters.json`.

The daily job is currently paused for the offseason (`if: false` in
`.github/workflows/daily.yml`). Remove that line when you want the schedule
to run again. Until then, a manual **Run workflow** is also skipped.

---

## Manual refresh
If you want to trigger an update manually (e.g. to test it):
1. Go to your GitHub repo → **Actions** tab
2. Click **MLB Stats Daily Update** → **Run workflow**
