# Deploy on Huawei Cloud CCE

Region **me-east-1 (Riyadh)**. Diagram: [hld/hld-relay-bot-cce.svg](hld/hld-relay-bot-cce.svg).

```
GitHub (main) ──Actions──▶ Docker Hub docker.io/ibn3bbas/youtube-relay-bot (private)
                                │ pull via NAT (Secret dockerhub-pull)
                                ▼
                     CCE ns relay-bot
                       └─ Deployment (1 pod, Recreate)
                          ├─ Secret  relay-bot-env   (env vars)
                          ├─ Secret  relay-bot-files (token.json, client_secret.json)
                          └─ PVC     relay-data (EVS) → /data
                     egress: NAT Gateway (SNAT) + EIP → Telegram / YouTube
```

No Service, no Ingress, no LB. The bot only dials out.

## 0. Prereqs

- CCE node subnet has a **NAT Gateway (SNAT rule) + EIP**, otherwise the pod has no internet.
- Docker Hub repo `ibn3bbas/youtube-relay-bot` created as **private** (a repo auto-created by a push follows the account default, usually public).
- Two Docker Hub personal access tokens (*Account settings → Personal access tokens*):
  **Read & Write** for GitHub Actions, **Read-only** for the cluster.
- GitHub repo secret `DOCKERHUB_TOKEN` (the Read & Write token). Username defaults to `ibn3bbas` (override with variable `DOCKERHUB_USERNAME`):
  ```bash
  gh secret set DOCKERHUB_TOKEN            # prompts; value stays out of shell history
  gh workflow run build-push-dockerhub --ref main && gh run watch
  ```
  Set it **before** merging to main, or the build fails at the settings check.
- `kubectl` pointed at the cluster (CCE console → cluster → kubectl → download kubeconfig).

## 1. Get the YouTube OAuth token once, on your laptop

The pod cannot open a browser. Do the OAuth dance locally:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                       # fill Telegram values
python -c "from src.uploader import _get_credentials; _get_credentials()"
# browser opens → approve → config/token.json written
```

## 2. Smoke-test YouTube from the cluster BEFORE anything else

Cloud IPs often get "Sign in to confirm you're not a bot". Check first:

```bash
kubectl create ns relay-bot
kubectl -n relay-bot run ytdlp-test --rm -it --restart=Never \
  --image=python:3.12-slim -- sh -c \
  "pip -q install yt-dlp && yt-dlp --simulate --print title 'https://www.youtube.com/watch?v=jNQXAC9IVRw'"
```

Title printed → good. `Sign in to confirm you're not a bot` → you need cookies (next section).
On the staging cluster (2026-11-14) the channel **listing** worked but every **video** hit the bot-check, and cookies fixed it.

## 2b. YouTube cookies (needed on cloud IPs)

Use a **throwaway Google account**, never the target channel's account or a personal one.

1. Edge or Chrome: install **Get cookies.txt LOCALLY** (Chrome Web Store, id `cclelndahbckbenkjhflpdbgdldlbecc`, open source).
   Then *Manage extensions → Details → Allow in InPrivate*.
2. InPrivate window → youtube.com → sign in with the throwaway account → play a video for ~20 s.
3. Extension → format **Netscape** → **Export** (not *Export All Cookies*).
4. Close the window **without signing out** (signing out kills the cookies).
5. Copy to the jumpbox, make it a Secret, and destroy every copy:
   ```bash
   scp -i <jumpbox-key> www.youtube.com_cookies.txt <user>@<jumpbox-eip>:~/cookies.txt && rm www.youtube.com_cookies.txt
   # on the jumpbox
   kubectl -n relay-bot create secret generic ytdlp-cookies --from-file=cookies.txt=$HOME/cookies.txt \
     --dry-run=client -o yaml | kubectl apply -f -
   shred -u ~/cookies.txt
   ```
   If the browser saved it under OneDrive, also empty it from the OneDrive recycle bin.

The pod reads `/cookies/cookies.txt` (`YTDLP_COOKIES_FILE`). It works on a copy in `/data/ytdlp-cookies.txt` so that
YouTube's cookie rotation persists, and it re-copies automatically when the Secret is replaced, with no restart needed.

**Refresh** when downloads start failing with the bot-check again (typically after weeks): repeat steps 2–5.

## 3. Secrets

```bash
# image pull — the read-only token
kubectl -n relay-bot create secret docker-registry dockerhub-pull \
  --docker-server=https://index.docker.io/v1/ \
  --docker-username=ibn3bbas \
  --docker-password="$(read -rsp 'Docker Hub read-only token: ' t; echo "$t")"
```

```bash
kubectl -n relay-bot create secret generic relay-bot-env --from-env-file=.env

# ytdlp-cookies: see section 2b

kubectl -n relay-bot create secret generic relay-bot-files \
  --from-file=token.json=config/token.json \
  --from-file=client_secret.json=config/client_secret.json
```

The token Secret is a bootstrap only: an initContainer copies it onto the EVS volume
the first time, and the app keeps the refreshed copy there.

## 4. Deploy

```bash
kubectl apply -f k8s/relay-bot.yaml
kubectl -n relay-bot logs -f deploy/relay-bot
```

Send `/status` to the bot on Telegram.

## 5. Facebook Page (optional second target)

Each relayed video is also posted to a Facebook Page (`src/fb_uploader.py`). Off unless `FB_ENABLED=true`.

1. Meta app (developers.facebook.com, use case "Manage everything on your Page", no business
   portfolio; stays in development mode) with `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`.
2. Never-expiring Page token: Graph API Explorer user token → exchange for a long-lived user token
   (`oauth/access_token?grant_type=fb_exchange_token` with the app secret) → `me/accounts` → the
   Page's `access_token`. Check it in the Access Token Debugger: type PAGE, Expires: Never.
3. Add `FB_ENABLED`, `FB_PAGE_ID`, `FB_PAGE_TOKEN` to `relay-bot-env`, then restart:

```bash
kubectl -n relay-bot patch secret relay-bot-env --type merge -p \
  "{\"stringData\":{\"FB_ENABLED\":\"true\",\"FB_PAGE_ID\":\"<page id>\",\"FB_PAGE_TOKEN\":\"$FB_PAGE_TOKEN\"}}"
kubectl -n relay-bot rollout restart deploy/relay-bot
```

Videos already on YouTube before Facebook was enabled: send `/fbfill [n]` in Telegram
(oldest first, default 3, max 10 per command).

A failed Facebook post is queued in `/data/facebook_state.json` and retried on later runs
(Facebook only, YouTube is not re-uploaded). A token/permission error shows as ⚠️ in Telegram.

### 5a. Reels and warm-up pacing

- **Format** (`FB_FORMAT`, default `auto`): clips of 3–90 s go up as Reels (`/video_reels` →
  `rupload.facebook.com` → finish). Short landscape/square clips are letterboxed to 1080x1920 in the
  pod first (`FB_REELS_PAD`). Longer videos can't be Reels through the API and stay normal video posts.
  A failed Reel falls back to a normal post. The Reels API allows 30 posts per Page per 24 h.
- **Warm-up** (`FB_WARMUP_SCHEDULE=1,3`, then `FB_DAILY_LIMIT=8`): week 1 one post a day, week 2 three,
  then up to 8. Week 1 starts on `FB_WARMUP_START` or the first day after rollout. Posts are spaced
  16 h / cap apart (minimum `FB_MIN_GAP_MINUTES`). Videos held back go to the pending queue and post
  on later runs. `/fbfill` respects the daily cap but not the spacing.
- Telegram reports show `FB today: n/cap (warm-up week k), q queued`.

Change any of these without a rebuild:

```bash
kubectl -n relay-bot patch secret relay-bot-env --type merge -p \
  '{"stringData":{"FB_WARMUP_SCHEDULE":"1,3","FB_DAILY_LIMIT":"8","FB_FORMAT":"auto"}}'
kubectl -n relay-bot rollout restart deploy/relay-bot
kubectl -n relay-bot exec deploy/relay-bot -- python -c \
  "import json;s=json.load(open('/data/facebook_state.json'));print(s['counts'],len(s['pending']),s['warmup_start'])"
```

## 6. Instagram (optional third target: Shorts only)

Only the source channel's **Shorts** go to Instagram, as Reels (`src/ig_uploader.py`). They come from the
channel's Shorts tab. The YouTube relay reads only the Videos tab, so Shorts never reach YouTube or Facebook.
Off unless `IG_ENABLED=true`.

Platform limits (Meta docs, checked 2026-11-18): Reels 3 s – 15 min, ≤300 MB, H.264/HEVC, 23–60 fps,
≤1920 px wide, AAC ≤48 kHz, moov atom first, no edit lists. 100 API posts per rolling 24 h
(`content_publishing_limit` reports 50). YouTube Shorts are ≤3 min, so `IG_MAX_SECONDS=180`.
Clips that already fit are only remuxed. Others (e.g. 15 fps or 44.1 kHz audio) are re-encoded in the pod.

1. Instagram app → Settings → Account type → switch to **Professional** (Business or Creator), then link
   it to the Facebook Page (Page settings → Linked accounts → Instagram).
2. Meta app → add the Instagram permissions `instagram_basic` and `instagram_content_publish`
   (Instagram API with **Facebook Login**). Resumable upload works only with Facebook Login for Business,
   not "Instagram Login".
3. Make a new never-expiring Page token the same way as 5.2, ticking the two new permissions as well.
   It replaces `FB_PAGE_TOKEN` (Facebook keeps working), or put it in `IG_ACCESS_TOKEN`.
4. Patch the Secret, restart, find the IG user id, then run the hidden upload test (never published):

```bash
kubectl -n relay-bot patch secret relay-bot-env --type merge -p '{"stringData":{"IG_ENABLED":"true"}}'
kubectl -n relay-bot rollout restart deploy/relay-bot
kubectl -n relay-bot exec deploy/relay-bot -- python -m src.ig_uploader check          # prints instagram_business_account id
kubectl -n relay-bot patch secret relay-bot-env --type merge -p '{"stringData":{"IG_USER_ID":"<id>"}}'
kubectl -n relay-bot rollout restart deploy/relay-bot
kubectl -n relay-bot exec deploy/relay-bot -- python -m src.ig_uploader check --upload # test container, not published
```

5. In Telegram: `/igfill 1` posts the newest Short not yet on Instagram.

Pacing: each scheduled check posts at most one Short, at least `IG_MIN_GAP_MINUTES` (180) after the last
post and at most `IG_DAILY_LIMIT` (3) a day. New Shorts go first. With `IG_TOPUP=true`, older Shorts
(newest first) fill the rest of the cap. `/igfill [n]` (max 5) ignores the gap but keeps the cap. A failed
Short is picked up again on a later run. Clips that can never fit are recorded as `skipped`.
State: `/data/instagram_state.json`.

## 7. X (optional fourth target: Shorts only)

The same source Shorts go to X as video posts (`src/x_uploader.py`, API v2 chunked media upload,
OAuth 1.0a user context). Post text = the video title only; URLs are stripped because X bills
~$0.015 per post and $0.20 for a post with a link. Off unless `X_ENABLED=true`.

Platform limits (docs.x.com, checked 2026-11-18): 0.5 s – 20 min on a normal account, MP4 H.264 +
AAC, yuv420p, ≤60 fps, ≤1920x1200 / 1200x1920. Clips that fit are only remuxed, others re-encoded.

1. console.x.com → developer account (pay-per-use, buy credit, auto-recharge off) → app →
   User authentication settings: **Read and write**, Web App/Automated App or Bot. The callback is
   unused but required; `https://localhost` is rejected, so use any real https URL.
2. Keys & Tokens → Consumer Key/Secret. The console's own Access Token is for the account that owns the
   developer account. To post to a **different** account, run an OAuth 1.0a PIN flow with the Consumer
   Key and open the authorize URL in a private window logged in as the posting account.
3. Secret: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`, then test before enabling:

```bash
kubectl -n relay-bot exec deploy/relay-bot -- python -m src.x_uploader check           # prints the posting @handle
kubectl -n relay-bot exec deploy/relay-bot -- python -m src.x_uploader check --upload  # media upload only, not posted
kubectl -n relay-bot patch secret relay-bot-env --type merge -p '{"stringData":{"X_ENABLED":"true"}}'
kubectl -n relay-bot rollout restart deploy/relay-bot
```

4. In Telegram: `/xfill 1`.

Pacing is the same as Instagram: `X_DAILY_LIMIT` (3), `X_MIN_GAP_MINUTES` (180), `X_TOPUP`, `/xfill [n]`
(max 5). HTTP 401/402/403 (bad keys, no credit, no write access) stop X for the run and show in Telegram;
429 waits for a later run. If X refuses a post as duplicate text (same title twice), it retries once with
the upload date appended. State: `/data/x_state.json`.

## 8. TikTok (optional fifth target: Shorts only)

The same source Shorts go to TikTok (`src/tiktok_uploader.py`, Content Posting API, Direct Post,
`FILE_UPLOAD` in 10 MB chunks). Post title = the video title, links stripped. Off unless `TIKTOK_ENABLED=true`.

Limits (developers.tiktok.com): MP4 H.264, 23–60 fps, 360–4096 px per side, ≤ 4 GB, length ≤ the creator's
`max_video_post_duration_sec` (read from `creator_info` before every post; 3 min for most accounts). Low-fps
(15 fps) Shorts are re-encoded to 30 fps; clips that already fit are only remuxed.

**Unaudited apps post `SELF_ONLY` (private) only**, so `TIKTOK_PRIVACY=SELF_ONLY` until TikTok's app review
passes; then set `TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE` and switch to the production client key/secret
(the token must then be made again with the production keys).

1. developers.tiktok.com → Manage apps → Connect an app (type Other). Terms/Privacy/Web URL on the Pages site;
   verify them under **URL properties → URL prefix** (signature `.txt` file published on `gh-pages`).
2. Sandbox: Login Kit (redirect URI `…/tiktok-callback.html` on the Pages site; type it by hand, a pasted
   hidden character gives "Invalid Redirect Uri"), Content Posting API with **Direct Post** on, scopes
   `user.info.basic` + `video.publish`, the posting account under Target Users. `Verify domains` is only for
   `PULL_FROM_URL`, not needed.
3. Laptop: `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` in `.env`, then a one-time Login Kit authorization as the
   posting account; the callback page shows the code, which is exchanged for an access token (24 h) and a
   refresh token (365 days) → `tiktok_token.json`.
4. Secret: `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`. Seed the token onto the volume and test before enabling:

```bash
kubectl -n relay-bot exec -i deploy/relay-bot -- python -m src.tiktok_uploader seed < tiktok_token.json
kubectl -n relay-bot exec deploy/relay-bot -- python -m src.tiktok_uploader check   # @handle, privacy options, max length
kubectl -n relay-bot patch secret relay-bot-env --type merge -p '{"stringData":{"TIKTOK_ENABLED":"true"}}'
kubectl -n relay-bot rollout restart deploy/relay-bot
```

5. In Telegram: `/ttfill 1`.

The bot refreshes the access token itself and rewrites `/data/tiktok_token.json` (the refresh token may rotate),
so the token must stay on the persistent volume, not in the Secret. Redo step 3–4 before the refresh token
expires (`check` prints the days left). Pacing as Instagram/X: `TIKTOK_DAILY_LIMIT` (3),
`TIKTOK_MIN_GAP_MINUTES` (180), `TIKTOK_TOPUP`, `/ttfill [n]` (max 5). Token/scope/privacy errors stop TikTok for
the run and show in Telegram; rate/spam limits wait for a later run. State: `/data/tiktok_state.json`.

## Day 2

```bash
# new image after a push to main
kubectl -n relay-bot rollout restart deploy/relay-bot

# what has been relayed
kubectl -n relay-bot exec deploy/relay-bot -- cat /data/processed_videos.json

# token got revoked → re-auth locally, then replace the copy on the volume
kubectl -n relay-bot cp config/token.json "$(kubectl -n relay-bot get pod -l app=relay-bot -o name | cut -d/ -f2)":/data/token.json
```

## Rules that matter

- `replicas: 1` and `strategy: Recreate` — never change. Two pods = the same video uploaded twice.
- YouTube API quota ≈ 6 uploads/day. More pods or bigger nodes don't change that.
- EVS is zonal: the pod follows its volume's AZ. Fine for one pod.
