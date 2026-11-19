# Open items

Where the Huawei Cloud CCE deployment stands. Last updated **2026-11-18**.
Runbook: [docs/deploy-cce.md](docs/deploy-cce.md) · Diagram: [docs/hld/hld-relay-bot-cce.svg](docs/hld/hld-relay-bot-cce.svg)

> This repo is **public** (recreated clean 2026-11-18). Never write tokens, IPs, hostnames, SSH users/keys or account names here.
> Use placeholders such as `<jumpbox-eip>`.

## Done

- [x] Image builds and pushes to private Docker Hub `ibn3bbas/youtube-relay-bot` (workflow `build-push-dockerhub`, linux/amd64, includes deno)
- [x] GitHub secret `DOCKERHUB_TOKEN` set (Read & Write). A read-only token for the cluster exists.
- [x] Cluster egress verified: Telegram, YouTube, googleapis and Docker Hub are all reachable through the NAT
- [x] YouTube bot-check on the cloud IP solved with a throwaway account's cookies + deno; Secret `ytdlp-cookies` is in ns `relay-bot`
- [x] Bot code supports `YTDLP_COOKIES_FILE`; the manifest mounts the cookies Secret
- [x] `processed_videos.json` prepared: "start from now", 60 current source IDs marked (none had been relayed before)

## Deployed (2026-11-15)

- [x] Account unfrozen. The PVC was recreated on `csi-disk-topology` (a separate disk, not shared with other workloads); pod running
- [x] Processed list (60 "start from now" IDs) copied into `/data/`; `/status` answers
- [x] First full relay confirmed: download through cookies → upload → Telegram report (Found 1, Uploaded 1)
- [x] Cookie expiry symptom seen: the log shows `Sign in to confirm you're not a bot` and `Metadata fetch failed`; refreshed per runbook 2b

## Schedule change: frequent checks + daily top-up (2026-11-17, merged and rolled out)

Why: the source posts ~1 video/day, often after the 18:00 run, so 06:00/18:00 runs mostly found 0 and the
day ended with 1 upload. Now: check every 2h (silent unless something uploads / bot-check / quota), and at
09:00 Riyadh top up to 6 uploads/quota-day with older source videos (newest first). New `/fill` command.
Counts per YouTube quota day (Pacific) in `/data/upload_counts.json`. Config: `CHECK_INTERVAL_MINUTES`,
`DAILY_UPLOAD_TARGET`, `BACKFILL_TIME`, `BACKFILL_SCAN_LIMIT`.

- [x] Code + docs, offline-tested with yt-dlp/uploader stubbed (new relay, top-up to 6, target met, quota abort, job times)
- [x] Confirm the `build-push-dockerhub` run for the merge commit is green
- [x] Roll out (2026-11-17 20:25): `kubectl -n relay-bot rollout restart deploy/relay-bot`, then `/next` should list `check_new` and `daily_topup` (confirmed)
- [x] `/fill` tried 2026-11-17: 6 older videos uploaded, `Today: 6/6`
- [ ] Next morning: check the 09:00 "Daily top-up done" report; watch for quota aborts on the first day (counter starts at 0)
- [x] Removed `SCHEDULE_MORNING`/`SCHEDULE_EVENING` from the `relay-bot-env` Secret if present (now ignored)

## Facebook Page second target (2026-11-18, live)

Each relayed video is also posted to the owner's Facebook Page. Runbook section 5.

- [x] `src/fb_uploader.py` (Graph API resumable upload) + per-platform state; offline-tested (retry queue, auth error, quota)
- [x] Meta app (dev mode, Pages use case) + never-expiring Page token; hidden unpublished test upload OK and deleted
- [x] `FB_*` added to Secret `relay-bot-env`, rolled out; pod reaches the Graph API
- [x] `/fbfill [n]` posts already-relayed videos, oldest first; first post confirmed on the Page
- [x] Post text now starts with the video title (the feed doesn't show the title field); first post fixed in place
- [ ] Confirm the first automatic relay reports `Facebook: 1 posted`

## Facebook Reels + warm-up pacing (2026-11-18, code merged, not rolled out)

The Page was dormant for a long time; a sudden burst of posts looks like a hijacked account. Commit `3f43e57`,
image built green. Runbook section 5a.

- [x] Reels: 3–90 s clips via `/video_reels` → `rupload.facebook.com` → finish; short landscape clips padded to
      1080x1920 with ffmpeg; longer videos stay normal posts; failed Reel falls back to a normal post (`FB_FORMAT=auto`)
- [x] Warm-up: `FB_WARMUP_SCHEDULE=1,3` (week 1: 1/day, week 2: 3/day), then `FB_DAILY_LIMIT=8`; posts spaced
      16h / cap apart (min `FB_MIN_GAP_MINUTES=90`); held-back videos queue in `facebook_state.json` `pending`
- [x] Telegram report: `Facebook: n posted (r Reels), f failed, h held back` + `FB today: n/cap (warm-up week k), q queued`
- [x] Offline tests: pacing over simulated days, real ffmpeg probe/pad, mocked Graph/rupload, Reel→video fallback, deferred ≠ failed
- [ ] Roll out: `kubectl -n relay-bot rollout restart deploy/relay-bot`, then `/run`; check the `FB today` line
- [ ] First real Reel: the Reels endpoints are untested against the live Page. If the source has no ≤90 s clips,
      test with a hidden post (`video_state=DRAFT` or `published=false` then `DELETE`)
- [ ] Watch the queue: it grows ~5/day during warm-up (YouTube ~6/day), drains ~2/day at 8/day
- [ ] Optional: vertical 60 s teaser Reel cut from each long video (the API rejects Reels over 90 s), linking to the full post

## Next: rename to "Multi-Relay Bot" + more platforms (planned)

Name chosen 2026-11-18: **Multi-Relay Bot**. Repo renamed 2026-11-19.

Rename (keep infra names to avoid churn):
- [x] Rename the GitHub repo to `Project-12-Multi-Relay-Bot` (2026-11-19; old URLs redirect); README title, `/help` text, runbook/skill references updated
- [ ] Update the Google OAuth app's home + privacy page URLs (GitHub Pages path follows the repo name), then re-check the consent screen
- [ ] Keep as is: Docker image `ibn3bbas/youtube-relay-bot`, namespace `relay-bot`, Telegram bot username

Expansion, one platform at a time (same pattern as Facebook: own uploader module, `<PLATFORM>_ENABLED` flag,
per-platform done/pending state, Telegram summary line, `/<platform>fill` backlog command, hidden test first):
- [x] **Instagram** code (2026-11-18): source **Shorts tab only** → Reels (`src/ig_uploader.py`, runbook §6),
      resumable upload, remux/re-encode to the Reels spec, `IG_DAILY_LIMIT=3` with a 180 min gap, `/igfill [n]`,
      hidden self-test `python -m src.ig_uploader check --upload`. Offline-tested (mocked Graph API, ffmpeg mocked).
- [x] Instagram setup (2026-11-19): Professional account linked, new Page token with IG scopes, Secret updated,
      `check --upload` green, first Reel posted automatically 00:21 Riyadh
- [x] rupload 400 `ProcessingFailedError` on 18–22 MB Shorts while the container is FINISHED → trust the container (`7246495`)
- [x] Bot keeps relaying when the Telegram Bot API is down (per-bot 502 outage 2026-11-19, `ccfba0c`)
- [x] Large Short (18.6 MB, the one that failed before) posted after the fix via `/igfill 1` (2026-11-19 01:10)
- [ ] **TikTok**: Content Posting API; app audit required before posts can be public (unaudited apps post private-only).
      Length (checked 2026-11-18): the API accepts up to 10 min, but each creator has their own cap
      (`max_video_post_duration_sec` from creator_info: 3 min for everyone, 5 or 10 min for some). Read it per post. ≤4 GB, 23–60 fps.
- [ ] **X**: video in a post (checked 2026-11-18, docs.x.com): 0.5 s – 20 min / 8 GB on a normal account,
      up to 125 min / 16 GB with X Premium (limit follows the posting account, not the API plan). The API is
      pay-per-use (~$0.015 per post created); chunked media upload. Shorts (≤180 s) fit easily.
- [ ] Refactor once a third target lands: a common uploader interface + generic per-platform state, instead of copying the Facebook code

## SaaS MVP plan (drafted 2026-11-18, not started)

Goal: turn Multi-Relay Bot into a first business. Full plan lives in a private Claude doc ("Multi-Relay Bot — MVP Plan").

**Niche.** Arabic-first, Telegram-controlled "upload once, publish everywhere" for organizations that **own** their
video: mosques, scholars' offices, Islamic/Arabic education channels. Incumbents (Repurpose.io, ShortSync, Later,
Publer) are English-first web dashboards in USD. Customers relay only content they own: YouTube's reused-content
policy applies even with the creator's permission and demonetizes the whole channel. The friend-channel relay stays a personal tool.

**MVP scope.** Source = file sent to the Telegram bot or a Google Drive folder (no yt-dlp, cookies or bot-checks).
Targets = YouTube, Facebook Page, Instagram Reels. Out: TikTok (until audit), X, AI clipping, analytics, teams.
Telegram's hosted Bot API caps bot downloads at 20 MB → self-host the Bot API server (2 GB) or use Drive links.

**Pricing (to test with first 10 customers).** Starter 49 SAR/mo (1 workspace, 3 platforms, 30 videos) ·
Pro 149 SAR/mo (unlimited videos, backlog fill) · Organization 499 SAR/mo (up to 10 workspaces). Yearly = 2 months free.
First 3 customers: free done-for-you setup instead of a free tier.

**API approvals (file first, they take longer than the code).**
- YouTube: Google OAuth verification for the upload scope + YouTube Audit and Quota Extension Form. Unverified projects upload private only. Google's quota page now lists `videos.insert` as its own bucket of 100 calls/day per project, shared by all customers. Weeks to months.
- Meta (Facebook + Instagram): Business Verification + App Review for Page and Instagram publish permissions; one app covers both. ~1–4 weeks.
- TikTok: Content Posting API audit; until then posts are private and max 5 users/24h. Several days to 2 weeks.
- X: no approval; pay per use ($0.015/post, $0.20 with a link). The "paid tier" note above is outdated.

**Architecture changes (single tenant → SaaS).**
- Drop `replicas: 1` + `Recreate`: stateless upload workers (HPA) with a per-workspace lock in Postgres/Redis
- Tokens: `.env`/K8s Secrets → per-workspace OAuth tokens in Postgres, encrypted with Huawei DEW
- State: `processed_videos.json`/`upload_counts.json` on PVC → Postgres tables (videos, platform_posts, quota_usage)
- Scheduling: APScheduler in-process → Redis queue; quota tracked per Google project
- Telegram: long polling, one chat → webhook gateway, chat ID mapped to a workspace
- Files: PVC downloads → OBS bucket with 7-day lifecycle
- Keep: NetworkPolicy, NAT egress, non-root image, Docker Hub build pipeline

**90-day plan.**
- [ ] Days 1–30: register the business entity; privacy policy + terms on GitHub Pages; submit Google OAuth verification and Meta Business Verification + App Review; interview 10 target organizations
- [ ] Days 31–60: multi-tenant core (workspaces in Postgres, encrypted tokens, Arabic Telegram onboarding, file intake); 3 free pilots on Facebook + Instagram
- [ ] Days 61–90: YouTube once the audit passes, start billing, apply for the TikTok audit

Kill criteria: by day 90, fewer than 3 pilots convert to paid, or Google/Meta reject the app twice.

Open questions:
- [ ] Source: customers send files (planned) or also pull from the customer's own YouTube channel?
- [ ] Verify the real YouTube upload limit in Cloud Console (old docs say ~6/day, Google's quota page now says 100 calls/day)

## Still open

- [x] Old retained EVS disk from the first PVC deleted (2026-11-18)
- [x] Bot-check failures now count as Failed in the Telegram report, with a "cookies likely expired" hint
- [x] `kubeconform -strict -summary k8s/relay-bot.yaml`: 4 valid, 0 invalid (2026-11-17)

## Cleanup

- [x] Unused GitHub secrets `SWR_REGISTRY_USER` / `SWR_REGISTRY_PASS` deleted
- [x] Old *Public Read-only* `github-actions-push` Docker Hub token: already gone
- [x] Docker Hub "desktop" tokens: 4 old ones revoked, newest kept (2026-11-18)
- [x] Empty the exported cookies file from the OneDrive recycle bin (2026-11-18)
- [x] Infrastructure details in git history: rewritten 2026-11-18 (see below)
- [x] GitHub Pages (home + privacy) back online

## Git history cleanup (2026-11-18)

History was rewritten with `git filter-repo` (infra IPs, host/cluster/key/repo names replaced with placeholders;
commit email mapped to the GitHub noreply address) and force-pushed. No passwords, tokens or keys were ever committed.

- [x] Rewrite + force-push `main` and `gh-pages`; local clone reset and old objects purged
- [x] GitHub Support purge: declined (free plan). Old repo **deleted** and recreated public from the clean history, so PR refs and old commits are gone (2026-11-18)
- [x] Check forks: none (2026-11-18)
- [x] ~~Change the jumpbox public IP~~ skipped: port 22 is allow-listed, SSH is key-only with fail2ban, so a known IP gives no access
- [x] Repo public again, Pages on `gh-pages`, secret scanning + push protection on (2026-11-18)
- [x] `DOCKERHUB_TOKEN` re-entered; manual build-push run green (2026-11-18)
