# Vercel cutover

The dashboard stays a static site. `vercel.json` rewrites every path that is not a real file to `/mlb_daily_stats.html`, and sets `Cache-Control: no-cache, must-revalidate`. Files the page actually requests stay on the deployment: `mlb_daily_stats.html`, `pc_photos.js`, `manifest.json`, `sw.js`, `icon-192.png`, `icon-512.png`, and `mickey.png`. There is no build command.

The daily GitHub Action still commits to `main`. Vercel Git integration deploys that push. Do not add a Vercel token or any Vercel secret to the workflow.

Firebase Hosting no longer serves the dashboard. `firebase.json` points hosting at `redirect/`, a one-page meta-refresh and `location.replace` to `https://mlb-daily-stats.vercel.app` that keeps the path, query, and hash. The workflow still runs `firebase deploy --only hosting` so that redirect stays published. The upload is only that folder, and it does not touch Firestore rules.

## One-time Vercel setup

1. In Vercel, import this GitHub repo. Framework preset: **Other**.
2. Leave the build command empty and do not set an install command. The project root is the site.
3. Production branch: `main`. Each daily commit on `main` becomes the production deployment.
4. Deploy. Copy the production hostname (for example `mlb-daily-stats.vercel.app`). Preview URLs on other branches are separate hostnames.

## Firebase Authentication

Email/password sign-in fails on a host that is not an authorized domain (`auth/unauthorized-domain`).

1. Firebase console → project `mlb-stats-ae429` → Authentication → Settings → **Authorized domains**.
2. Add the production `vercel.app` hostname from the step above. Add a custom domain too, if you attach one.
3. Firebase does not accept a wildcard. Each hostname has to be listed. The daily site is the production domain, so that one is the one that matters.

Auth stays email/password. User documents stay in Firestore. Nothing in the workflow changes the Firebase project.

## Firestore rules (so two accounts cannot take the same team)

The picker writes `users/{uid}.espnTeamId` and a `teamClaims/{teamId}` document. The claim document is what blocks a second account. Those rules are in `firestore.rules`. The daily workflow deploys **hosting only**, so it will not publish them.

Publish once, either by pasting `firestore.rules` into Firebase console → Firestore → Rules, or:

```
firebase deploy --only firestore:rules --project mlb-stats-ae429
```

Until that publish, a signed-in user can still link a team on their own user document, and the picker cannot see which teams are taken.

## Old Firebase URL

`https://mlb-stats-ae429.web.app` (and the `.firebaseapp.com` host) should send people to the Vercel site. That happens because the daily job deploys `redirect/` and only `redirect/`. Leaving the step in the workflow is intentional: it is cheap, and the next run puts the redirect back if hosting is overwritten. Removing the step after a single deploy would not.

## Reset a locked ESPN team

There is no change-team control. To undo a pick:

1. Firebase console → Firestore → `teamClaims` → delete the document whose id is the ESPN team id (1–10 for this league).
2. Firestore → `users` → that person's document → delete the fields `espnTeamId` and `espnTeamName`.
3. Have them sign in again. The picker shows, and that team can be claimed again. Their old manual roster is still on the user document and is what My Team shows until a team is linked.
