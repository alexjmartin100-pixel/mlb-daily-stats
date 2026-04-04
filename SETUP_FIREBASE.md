# MLB Stats Web App — Firebase Setup Guide

One-time setup (~15 minutes). After this, the script auto-deploys to Firebase
every morning and your dashboard is live at a public URL on any device.

---

## What you need
- Your Google / Gmail account (you already have this)
- Node.js installed on your PC (free, one install)

---

## Step 1 — Install Node.js
1. Go to **https://nodejs.org**
2. Click the big **LTS** download button (the recommended version)
3. Run the installer, click Next through all the defaults
4. When done, open **Command Prompt** (search "cmd" in Start menu) and type:
   ```
   node --version
   ```
   You should see a version number like `v20.x.x` — that means it worked.

---

## Step 2 — Install Firebase CLI
In Command Prompt, paste this and press Enter:
```
npm install -g firebase-tools
```
Wait for it to finish (30–60 seconds).

---

## Step 3 — Log in to Firebase with your Google account
In Command Prompt:
```
firebase login
```
A browser window will open — sign in with your Gmail account and allow access.

---

## Step 4 — Create a Firebase project
1. Go to **https://console.firebase.google.com**
2. Click **Add project**
3. Name it: `mlb-stats` (or anything you like)
4. Disable Google Analytics (not needed) → click **Create project**
5. Wait for it to finish → click **Continue**

---

## Step 5 — Enable Hosting
1. In your Firebase project, click **Hosting** in the left sidebar
2. Click **Get started** → click through the setup wizard (just click Next/Continue)
3. You can skip all the "install CLI" steps since you already did that

---

## Step 6 — Connect your folder to Firebase
Open Command Prompt and navigate to your MLB Daily Results folder:
```
cd "C:\Users\alexj\OneDrive\Documents\Claude\Projects\MLB Daily Results"
```

Then run:
```
firebase init hosting
```

When it asks questions, answer:
- **Which Firebase project?** → select your `mlb-stats` project
- **What do you want to use as your public directory?** → type `.` (just a dot) and press Enter
- **Configure as a single-page app?** → type `N` and press Enter
- **Set up automatic builds with GitHub?** → type `N` and press Enter
- **File mlb_daily_stats.html already exists. Overwrite?** → type `N` and press Enter

This creates a `.firebaserc` file that links your folder to your Firebase project.

---

## Step 7 — Deploy for the first time
```
firebase deploy --only hosting
```

When it finishes, it will show your URL — something like:
```
Hosting URL: https://mlb-stats-12345.web.app
```

**Bookmark that URL on your phone!** That's your dashboard.

---

## How it works going forward
- Your Python script runs every morning at 10:07 AM via Task Scheduler
- It generates the dashboard HTML, then **automatically runs Firebase deploy**
- Your live URL updates within seconds
- No action needed from you — just open the URL any time to see last night's stats

---

## Add to your phone's home screen

**iPhone (Safari):**
1. Open your URL in Safari
2. Tap the Share button (box with arrow) at the bottom
3. Tap **Add to Home Screen** → tap **Add**

**Android (Chrome):**
1. Open your URL in Chrome
2. Chrome may show an "Install" banner automatically — tap it
3. Or: 3-dot menu → **Add to Home screen**

The MLB Stats app icon will appear on your home screen and open full-screen.

---

## Your live URL format
```
https://YOUR-PROJECT-ID.web.app
```
Find it any time at console.firebase.google.com → your project → Hosting.
