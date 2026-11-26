---
name: relay-bot-cce-deploy
description: Continue or operate the Multi-Relay Bot (formerly YouTube Relay Bot) on Huawei Cloud CCE me-east-1 (k8s/relay-bot.yaml, build-push-dockerhub workflow gated by pytest, Docker Hub image, HLD diagram, runbook), including the Facebook Page target (fb_uploader, /fbfill), Instagram (Shorts as Reels, 720p copies, /igfill), Facebook Stories (Shorts split into ≤60 s parts, /fbsfill), X (Shorts, OAuth 1.0a PIN flow, /xfill), TikTok (Content Posting API Direct Post, sandbox/app review submitted 2026-11-26, private-account rule, /ttfill), yt-dlp breakages from YouTube player changes, per-platform Arabic captions, /stats SQLite history, expiry reminders, watchdog and healthchecks.io.
---

# Multi-Relay Bot (formerly YouTube Relay Bot) on Huawei Cloud CCE

Repo: `ibn3bbas-sd/Project-12-Multi-Relay-Bot`. Everything is on `main`. PR #1 (`deploy/huawei-cce`) is merged.
Region: **me-east-1 (Riyadh)**. Bash only for this repo. No PowerShell.

## Architecture (keep it this simple)

Diagram: `docs/hld/hld-relay-bot-cce.svg`, theme-aware 1920×1080, in the style of the user's other HLD diagrams.
It is generated: edit `docs/hld/gen_hld.py`, run `python3 docs/hld/gen_hld.py`, and commit both files. Never hand-edit the SVG.

```
GitHub main --Actions--> Docker Hub docker.io/ibn3bbas/youtube-relay-bot:{latest,<sha>} (private)
                           --pull via NAT (Secret dockerhub-pull)--> CCE ns relay-bot
                                Deployment (1 pod, Recreate)
                                  Secret relay-bot-env   (env from .env)
                                  Secret relay-bot-files (token.json, client_secret.json)
                                  PVC relay-data (EVS, csi-disk-topology) -> /data
                              egress: NAT Gateway (SNAT) + EIP -> Telegram / YouTube / Facebook Graph API
```

No Service, Ingress or LB. The bot only dials out (Telegram long-polling + YouTube).

## Files

- `k8s/relay-bot.yaml`: Namespace, PVC (EVS 20Gi `csi-disk-topology` + disk tags), Deployment, NetworkPolicy (deny ingress; egress 53 + 443).
  - initContainer `seed-token` copies `/secrets/token.json` to `/data/token.json` only if it is missing.
  - Non-root uid 10001, `fsGroup` 10001, `readOnlyRootFilesystem`, emptyDir for `/app/logs` and `/tmp`.
  - `imagePullSecrets: dockerhub-pull` (docker-registry Secret holding a **read-only** Docker Hub token; it also covers the busybox pull, avoiding anonymous rate limits).
  - Image `docker.io/ibn3bbas/youtube-relay-bot:latest`.
- `.github/workflows/build-push-dockerhub.yml`: push to main (src/config/requirements/Dockerfile/workflow) or manual dispatch. Builds `linux/amd64`, `provenance: false`. A first step fails loudly if the token is missing.
  - Secret `DOCKERHUB_TOKEN` (Read & Write PAT). Optional variable `DOCKERHUB_USERNAME` (default `ibn3bbas`).
  - Create the Hub repo as **private** before the first push. A repo auto-created by a push follows the account default (public).
- `docs/deploy-cce.md` (runbook), `README.md` (embeds the HLD), `Dockerfile`, `.dockerignore`.
- `deploy/wsl/` + `docs/wsl-deployment.md`: the fallback path, no longer primary. `docker-compose.yml` is for local dev only.

## Hard rules

- `replicas: 1` + `strategy: Recreate`. Never change these: two pods means double uploads (`_relay_lock` only works inside one process).
- YouTube API quota is about 6 uploads per day. Scaling changes nothing.
- The pod cannot do the OAuth browser flow. Get `token.json` locally, then seed it through the Secret.
- Never commit `.env`, `config/token.json` or `config/client_secret*.json`.
- GitHub secret values cannot be read back by any API. Never try to exfiltrate them from another repo (for example with a workflow that echoes them). The user pastes them in with `gh secret set`.

## Lessons

- **This repo is public** (deleted and recreated from scrubbed history 2026-11-18; PRs #1/#2 are gone). Never commit IPs, hostnames, SSH users or keys, company cluster/org/repo names, account names or any secret. Cluster access details live in Claude's private project memory (`relay-bot-cluster-access`), not here. Docs say "the jumpbox" and `<jumpbox-eip>`.
- Runs #1 and #2 failed with `docker/login-action: Username and password required` because the secrets were never set. Set secrets **before** merging anything that triggers the build.
- Huawei SWR was dropped: the available AK/SK could push to an existing company org but could not create one (`403 SVCSTG.SWR.4030170`). Docker Hub is used instead. The old SWR repo secrets were deleted on 2026-11-15; only `DOCKERHUB_TOKEN` remains.
- Docker Hub's PAT dropdown defaults to **Repo Public Read-only**. That fails the push with `401 ... insufficient scopes` even though login succeeds. Push token: **Repo Read & Write**. Cluster pull token: **Repo Read-only**.
- From the cluster's NAT IP, the yt-dlp channel **listing** works but every **video** hits "Sign in to confirm you're not a bot". Fixed with cookies from a throwaway Google account + deno (newest source video resolved at 1080p).
- Cookie hygiene: export from InPrivate with the **throwaway** account, never the target channel's (it was nearly exported once). Close without signing out. The browser may save to the OneDrive Desktop: delete it and empty the OneDrive recycle bin. Shred the jumpbox copy.
- Secret-volume gotchas: don't nest a mount under another Secret's mountPath, and with non-root `runAsUser` use `defaultMode: 0440` + `fsGroup`, because 0400 is root-only.
- Keep secrets out of OneDrive: the repo folder syncs to the company cloud. Build `.env` and the tokens in `~/relay-setup` (WSL ext4).
- kubectl only works on the jumpbox. Before giving the user commands, check which host their prompt shows: laptop vs jumpbox mix-ups happened twice.
- `/usr/bin/hcloud` on the laptop is the **Hetzner** CLI. For Huawei use KooCLI `hwcloud ... --cli-profile=<profile>` (profile name in private memory); it prints a "multi-version" banner, so `grep -v multi-version` before `jq`.
- Ask the user to run secret-entry steps themselves (hidden `read -s` prompts, `gh secret set`), so values never pass through Claude.

## Status and next steps

**LIVE since 2026-11-15.** The account freeze (2026-11-14) was lifted. First full relay confirmed
(download through cookies → upload → Telegram report). Checks for new videos every 2h (silent when nothing uploads); daily top-up to 6 uploads with older source videos at 09:00 Asia/Riyadh (before the Pacific-midnight quota reset). Per-quota-day counts in `/data/upload_counts.json`. Rolled out 2026-11-17; `/fill` tested (6/6).

Single checklist: **`open-items.md`**. Remaining: see `open-items.md`.

- All 4 Secrets exist in ns `relay-bot`: `dockerhub-pull`, `relay-bot-env`, `relay-bot-files`, `ytdlp-cookies`.
- PVC `relay-data` is on `csi-disk-topology` (Delete policy, own tagged disk, separate from company workloads).
- Google Cloud OAuth app is **In production** (home + privacy pages on `gh-pages`). Upload token scope is
  `youtube.upload` only, so `channels.list` returns 403 by design. The token auto-refreshes into `/data/token.json`.

Private details (hosts, IPs, local paths, account names, the cookie account) are in Claude's project memory, never here.

## Lessons from going live (2026-11-15)

- **Storage class:** the user wants the bot's disk separate from company business. Use `csi-disk-topology` (repo manifest),
  never `clickhouse-block` (a company class with **Retain** policy). A PVC's class is immutable: to switch, scale to 0,
  delete PVC + PV, then delete the retained EVS disk in the console (it keeps billing), then re-apply the manifest.
- Get the manifest onto the jumpbox with `curl` from raw.githubusercontent.com (repo is public) or `scp`.
- Copy `processed_videos.json` in right after the first start and check the count
  (`python -c "import json;print(len(json.load(open(...))))"`) before the next scheduled run.
- **Cookie expiry** looks like `Sign in to confirm you're not a bot` + `Metadata fetch failed` in the logs. The Telegram report
  now counts these as Failed and adds a "cookies likely expired" hint (commit `2fcfc92`). Refresh per runbook 2b; the pod
  picks up the replaced Secret in about a minute, no restart needed. Test with `/run`.
- **Don't suggest creating a new throwaway Google account:** the user's only phone number and the phone itself (QR
  verification) are both "used too many times". Reuse the existing cookie account (named in private memory); recover it
  via accounts.google.com/signin/usernamerecovery if needed.
- Claude can push the cookie file itself without reading it: `scp` to the jumpbox, `kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -`,
  `shred -u` there, `rm` the local file. The user still empties the OneDrive recycle bin.
- After a push to main: wait for `build-push-dockerhub`, then `kubectl -n relay-bot rollout restart deploy/relay-bot`.
- The bot answers `/start` with help; commands: `/run`, `/fill`, `/fbfill [n]`, `/status`, `/next`, `/help`.

## More lessons from the setup

- Multi-line pastes into `read -s` can swallow the next line. Have the user paste the `read` line alone, then the command.
- Screenshots leak secrets (a bot token was exposed once and revoked). Tell the user not to screenshot token or secret dialogs.
- Get the Telegram chat ID from the bot's own `getUpdates` after the user sends `/start`, rather than a third-party bot.
  Updates sent before a token revoke are gone.
- Google's OAuth "Publish app" stays greyed until Branding has a home page + privacy policy + authorized domain.
- A Docker Hub read-only PAT gets 401 if you ask the auth endpoint for `pull,push`. Test with `pull` only.

## Lessons from closing open items (2026-11-17/18)

- The upload counter starts at 0 on rollout, so uploads made earlier that quota day are not counted (day one saw 8 uploads, no quota error).
- Extract downloaded tool tarballs (e.g. kubeconform) in a scratch dir: they contain `LICENSE`/`README.md` and silently overwrite the repo's.
- `~/projects/Project-12-Multi-Relay-Bot` is a **symlink** to the OneDrive clone, not a second clone.
- Global git email is the company address; this repo has a **repo-local** `user.email` = GitHub noreply. Check `git log -1 --format=%ae` before pushing.
- History rewrite: `git filter-repo --replace-text F --replace-message F --mailmap M` on a `--mirror` clone, verify with `git log -p --all | grep`, `push --force --mirror` (`refs/pull/*` rejections are expected). Reset the working clone and `reflog expire` + `gc --prune=now` instead of deleting it (keeps ignored `data/`, `logs/`).
- PR refs keep old commits and only GitHub Support can drop them, but Support **declines on the free plan**. What worked: back up with `git bundle`, delete the repo in the web UI (gh lacks `delete_repo` scope), recreate with the same name, push `main` + `gh-pages`, re-enter `DOCKERHUB_TOKEN`. Never pick **Deletes** on the support form (it purges the whole repo).
- Making the repo private turns GitHub Pages off (free plan), taking the OAuth privacy page down. The token still refreshes.
- The jumpbox port-22 security-group allow-list grows with every new laptop IP (rules described `allow-my-ip: <date> for <host>`). Prune old ones with `hwcloud VPC DeleteSecurityGroupRule`; never touch rules with other descriptions (shared company SG).
- Jumpbox SSH is key-only (`/etc/ssh/sshd_config.d/00-hardening.conf`, the `00-` beats cloud-init's `50-`) with a fail2ban `sshd` jail (5 tries/10m, 1h ban). After `restart fail2ban`, wait a few seconds before `fail2ban-client status`.
- Before deleting a retained EVS disk, prove nothing references it: `kubectl get pv,pvc -A`, `volumeattachment`, and the PV `volumeHandle`, grepping both disk name and ID.
- Auto-mode may refuse edits to cluster Secrets; hand the user the exact `kubectl patch` command.

- After re-entering `DOCKERHUB_TOKEN`, prove it with `gh workflow run build-push-dockerhub.yml` + `gh run watch` (rebuilds the same code; harmless).
- `gh auth refresh -s delete_repo` uses a browser device flow that can end in `access_denied`; deleting the repo in the web UI (Settings → Danger Zone) is simpler.
- Before every push: scan the diff for infra IPs/names/emails (patterns in Claude's private memory) and check `git log -1 --format=%ae` is the noreply address. Secret scanning + push protection are on (free for public repos).
- Docker Hub tokens to keep: `github-actions-push` (Read & Write, the `DOCKERHUB_TOKEN` secret) and `cce-relay-bot-pull` (Read-only, Secret `dockerhub-pull`). Revoke stale auto-generated "desktop" tokens, keeping the newest.

## Rename to Multi-Relay Bot (2026-11-19)

- Repo renamed with `gh repo rename Project-12-Multi-Relay-Bot`, then `git remote set-url`. The old git URL redirects.
- Kept on purpose: Docker image `ibn3bbas/youtube-relay-bot`, namespace `relay-bot`, Telegram bot, GCP project `youtube-relay-bot`, this skill's name.
- **GitHub Pages does not redirect**: the old `/Project-12-YouTube-Relay-Bot/` path returns 404. After any rename, update Google Auth
  Platform → Branding home + privacy URLs to `https://ibn3bbas-sd.github.io/Project-12-Multi-Relay-Bot/` (+ `privacy.html`).
  The authorised domain `ibn3bbas-sd.github.io` stays. Status stays **In production** (token does not expire).
- Branding then shows "needs to be verified before it can be shown to users". Harmless for this single-user app; the
  token keeps refreshing. Never press "Back to testing" (Testing tokens expire every 7 days).
- The new `/help` title ("Multi-Relay Bot") only appears after the next image build + rollout.
- Local folder rename: close Claude Code first (its cwd is the folder), `mv` the OneDrive folder, recreate the
  `~/projects` symlink. WSL may keep a stale view of the renamed OneDrive folder (neither name visible while Windows shows
  the new one); `wsl --shutdown` from PowerShell fixes it.
- Claude memory is keyed by the folder path: copy `~/.claude/projects/<old-key>/memory` to the new key before reopening.
  Paths starting with `-` need `./` in shell commands.

## Facebook Page target (2026-11-18)

Each relayed video also goes to a Facebook Page (`src/fb_uploader.py`, runbook section 5). Off unless `FB_ENABLED=true`.
Page ID, app ID and account names are in Claude's private memory, never here.

- Flow in `_relay_one`: download once → YouTube → Facebook → cleanup. State in `/data/facebook_state.json`
  (`done` + `pending`). YouTube marks the video processed, so a failed Facebook post goes to `pending` and is retried
  Facebook-only (max `FB_RETRY_PER_RUN`) on later runs; YouTube is never re-uploaded. YouTube quota abort still posts
  to Facebook, then re-raises. Auth errors (codes 190/102/10/200) skip Facebook for the rest of the run + ⚠️ in Telegram.
- `/fbfill [n]` (default 3, max 10): already-relayed videos not yet on Facebook, oldest first, within
  `BACKFILL_SCAN_LIMIT`. Holds `_relay_lock`, so scheduled checks skip while it runs. Only new videos go automatically.
- **The feed shows only the post text (`description`), never the video `title` field.** The post text starts with
  the title, then the YouTube description, then the source credit. Fix an existing post in place:
  `POST /{video-id}` with `description=...`.
- Resumable upload on `graph-video.facebook.com/{ver}/{page}/videos`: `start` (file_size) → `transfer` chunks
  (`video_file_chunk`, follow returned offsets) → `finish` (title, description, published). Graph version is
  configurable (`FB_API_VERSION`, default v27.0; the Explorer shows the current one).
- Meta app setup: Get Started registers a developer account (no "My Apps" before that). Create App → filter
  **Content management** → "Manage everything on your Page" (not in Featured) → no business portfolio → create.
  Permissions `pages_manage_posts`, `pages_read_engagement`, `pages_show_list` ("Ready for testing" is enough in dev
  mode for the Page admin; `publish_video` is not offered and not needed). In the consent popup choose
  **"Opt in to current Pages only"**.
- Never-expiring Page token: Explorer **User Token** (dropdown → "Return to User Token") → exchange with the app
  secret (`oauth/access_token?grant_type=fb_exchange_token`) → `me/accounts` with the long-lived user token → Page
  `access_token`. `debug_token` must show `type PAGE, expires_at 0`. Exchanging a *Page* token only gives 60 days.
  Copy-pasting tokens from the Explorer is error-prone (placeholder left in, short-lived Page token copied):
  check type/expiry with `debug_token` before using.
- Test without touching the feed: upload with `published=false`, poll `status.video_status` until `ready`, `DELETE`.
- Put the token in the cluster without showing it: build the `{"stringData":{FB_*}}` JSON locally and pipe it over ssh
  into `kubectl patch secret relay-bot-env --type merge --patch-file /dev/stdin`, then `rollout restart`. Verify with
  a `/me` call from inside the pod. Changing the Facebook password or removing the app invalidates the token.
- The user states the source owner allows reposting; don't re-raise copyright.
- **Reels + warm-up (2026-11-18):** `fb_uploader.upload_video` returns `(id, "reel"|"video")`. Reels API only
  takes 3–90 s, 9:16, 30/24 h: longer source videos stay normal posts; short landscape clips are padded with ffmpeg.
  Pacing in `facebook_state.json` (`counts`, `last_post`, `warmup_start`): `pacing_block()` defers posts over
  today's cap (schedule `1,3` then `FB_DAILY_LIMIT` 8) or inside the 16h/cap spacing; deferred videos go to
  `pending`, counted as "held back", not failed. Runbook 5a.
- Rolled out 2026-11-18 (~11:39 Riyadh, image from `3f43e57`). First real `/fbfill`: a 354 MB / 31 min video
  downloaded, uploaded 0→100% in ~2.5 min through the normal `/videos` path, post text led by the title, processing
  → `ready` within minutes. Still to test: the first real short-clip Reel (Reels endpoints only mock-tested). Offline test harness pattern: set
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`, `SOURCE_CHANNEL_ID`, `DATA_DIR`, `FB_*` env, patch
  `fb_uploader._today` and `fb_uploader.requests.post`; generate clips with `ffmpeg -f lavfi -i testsrc`.

- **Facebook lists every Page video as a Reel**: `permalink_url` is `/reel/<id>` and the id shows in
  `GET /{page}/video_reels`, even for 31-min posts made via `/videos`. That's Meta's format, not a bot bug; don't
  "fix" it. To tell what the bot did, read its log line / return type, not the URL.
- `kubectl logs -f ... | grep facebook` looks "stuck" when nothing matches yet: it's following. Check
  `get pods` + `logs --tail`, then trigger `/fbfill 1` to see lines.
- Secrets stay out of OneDrive: the FB token lives in `~/relay-setup/.env` (WSL), never in the OneDrive repo's `.env`.
  uutils `shred -u` panics on the `/mnt/c` mount *after* overwriting and leaves a renamed `0000` file that is NOT
  gitignored: check `git status` for it and `rm` it. Then the user empties the OneDrive recycle bin.

## Instagram target: Shorts only (2026-11-18)

Commit `3ab20e7`; **LIVE since 2026-11-19 00:21 Riyadh** (first Reel posted automatically). Runbook §6.

- User's choice: only the source channel's **Shorts** (≤180 s) go to Instagram, as Reels.
- The bot's YouTube relay reads only `/channel/<id>/videos`. Shorts live on `/shorts` (400+ at the source,
  ~1–2/day, 58–142 s, mostly square, some 368px/15 fps), so Instagram lists the Shorts tab itself
  (`downloader.list_short_ids`). Shorts never go to YouTube or Facebook.
- `src/ig_uploader.py`: `POST /{ig-user}/media` (media_type=REELS, upload_type=resumable, share_to_feed) →
  `POST` the returned `rupload.facebook.com/ig-api-upload/...` uri (headers `Authorization: OAuth`, `offset: 0`,
  `file_size`) → poll `status_code` until FINISHED → `media_publish` (no blind retry; on error, check if the
  container shows PUBLISHED). Resumable works only with **Facebook Login** (not Instagram Login).
- ffmpeg: remux (`+faststart`, `-use_editlist 0`) when the file fits the spec, else re-encode (H.264, 30 fps when
  outside 23–60, ≤1920 wide, AAC 48 kHz). Caption = title + description + credit, ≤2200 chars, ≤30 hashtags.
- Pacing: one post per scheduled check, `IG_DAILY_LIMIT=3`/local day, `IG_MIN_GAP_MINUTES=180`;
  `IG_TOPUP=true` posts older Shorts (newest first) when nothing new. `/igfill [n]` (max 5) ignores the gap,
  keeps the cap. No retry queue: an unposted Short is picked up again. State `/data/instagram_state.json`
  (`done`, `skipped`, `counts`, `last_post`). Auth error → ⚠️ in Telegram; code 9 / subcode 2207042 → stops for 24 h limit.
- `IG_ACCESS_TOKEN` falls back to `FB_PAGE_TOKEN`; `IG_USER_ID` from `GET /{page}?fields=instagram_business_account`.
- Offline-tested with the Graph API and ffmpeg mocked. No ffmpeg on the laptop, and the auto-mode check refused
  downloading one. Real ffmpeg is exercised by the pod self-test.

**Progress (2026-11-18, 23:40 Riyadh): steps 1–2 DONE, step 3 in progress — resume at step 3.**

**Lessons from going live (2026-11-19):**
- Token made with `~/relay-setup/ig-token.py` (laptop; hidden prompts; exchanges the Explorer USER token, checks
  PAGE/expires 0/scopes/linked IG, writes `~/relay-setup/.env`, pipes the Secret patch over ssh, restarts, runs
  both checks). Consent popup: keep the Facebook profile (not the Instagram one in the account switcher),
  current Pages only, tick the business portfolio (Continue is greyed otherwise), current Instagram accounts only.
  Paste multi-line commands one at a time: a pasted newline fills the hidden prompt.
- Run `check --upload` only after the restart has settled: the first run hit the old pod as it was killed (exit 137).
- **rupload answers 400 `ProcessingFailedError` for real 18–22 MB Shorts while the container still reaches
  FINISHED.** Fixed in `7246495`: log it and trust the container status. Small clips (≤4 MB) never showed it.
- **Telegram per-bot outage** (getMe 502 after ~16 s, also from the laptop; a fake token gets 401 at once; many
  tdlib/telegram-bot-api issues since 2026-11-16, they recover by themselves). The bot crash-looped. Since
  `ccfba0c` `main.py` runs the same jobs on a plain APScheduler without Telegram, retries getMe every minute,
  then starts the normal bot and sends the saved reports.

- Some source Shorts exist only at 368x368 (YouTube's best format), so those Reels look soft. The user chose to
  post every Short anyway: **no minimum-resolution filter, don't offer it again.**
- Confirmed 2026-11-19 01:10: the 18.6 MB Short that failed before posted via `/igfill 1`.

**Original setup steps (done):**
1. ✅ Instagram → Professional account (Business), linked to the Facebook Page. Confirmed: Business Suite Page
   header shows "Edit Instagram Profile" + Instagram followers; asset reads "<Page>, ibn3bbas · Facebook, Instagram".
2. ✅ Meta app "Relay Bot": added use case **Manage messaging & content on Instagram** (API setup with
   **Facebook login**); `instagram_basic` + `instagram_content_publish` both "Ready for testing".
3. ⏳ New never-expiring Page token. In the Explorer the user had selected app Relay Bot, User Token, and the 6
   permissions `pages_show_list, pages_read_engagement, pages_manage_posts, business_management, instagram_basic,
   instagram_content_publish`; next: Generate → consent "Opt in to current Pages only", tick the Page **and** the
   IG account → exchange (`oauth/access_token?grant_type=fb_exchange_token&client_id=…&client_secret=…&fb_exchange_token=…`)
   → paste long-lived user token → `me/accounts?fields=name,access_token,instagram_business_account`
   (gives the Page token **and** `IG_USER_ID` in one call) → `debug_token`: PAGE, expires 0, scopes include
   `instagram_content_publish`. Token goes only in `~/relay-setup/.env` (WSL).
4. Jumpbox (paste the `read` line alone first):
   ```bash
   read -s FB_PAGE_TOKEN
   kubectl -n relay-bot patch secret relay-bot-env --type merge -p "{\"stringData\":{\"FB_PAGE_TOKEN\":\"$FB_PAGE_TOKEN\",\"IG_ENABLED\":\"true\"}}"
   kubectl -n relay-bot rollout restart deploy/relay-bot && kubectl -n relay-bot rollout status deploy/relay-bot
   kubectl -n relay-bot exec deploy/relay-bot -- python -m src.ig_uploader check
   ```
5. Put the printed `instagram_business_account` id in `IG_USER_ID`, restart, then
   `python -m src.ig_uploader check --upload` (5 s test clip, never published; container expires in 24 h).
6. Telegram `/igfill 1`; then confirm the first automatic post (`Instagram: 1 Shorts posted`, `IG today`).

If the token lacks Instagram scopes, `check` shows the Page lookup failing or `NOT LINKED`; if resumable is refused,
the app may need Facebook Login for Business (fallback would need a public `video_url`, not built).

### Lessons from linking Instagram to the Page (2026-11-18)

- "Can't Confirm — you must be an admin of the associated Page's business" = the Page is **owned by a business
  portfolio** and the Facebook profile doing the link isn't a portfolio member. Check Page → Settings → Page setup →
  Page access → "Owning business portfolio".
- Here the portfolio's only Full-access admin was the user's **Instagram-login identity** (portfolio created from
  the Instagram app), while the Facebook profile showed "Not in a business portfolio". Fix: in Business Suite
  switch profile to the IG identity → business.facebook.com/settings (portfolio) → Pages → Add the Facebook
  profile to the portfolio (Full access invite) → accept → link via Instagram accounts → Connect assets →
  Facebook Page → Review connection.
- The IG identity can't open Page views ("content isn't available"); switch back to the Facebook profile for
  Page screens. "Verification Required (new device)" on the confirm step is a security check; doing the link from
  the Facebook profile/home avoided it.
- A pending connection request canceled itself once the user acted as admin; just link directly as admin.
- Instagram web settings have no Page-link option (mobile app only: Edit profile → Page).
- `instagram_basic` / `instagram_content_publish` aren't in the "Manage Pages" use case; add the Instagram use
  case with **Facebook login**. Business/portfolio/app/IG IDs are private: never commit them.

## Next platforms: verified video limits (2026-11-18, official docs)

- **TikTok** (Content Posting API): API max 10 min, but per creator `max_video_post_duration_sec` from
  creator_info (all 3 min; some 5 or 10). ≤4 GB, 23–60 fps, 360–4096 px. Unaudited app → posts private only
  (`SELF_ONLY`) until TikTok's audit. User's plan: ≤10 min videos.
- **X** (docs.x.com): video in a post 0.5 s–20 min / 8 GB normal account, up to 125 min / 16 GB with Premium;
  the limit follows the posting account. Chunked upload. Pay-per-use API, ~$0.015 per post. User's plan: Shorts only.
- Instagram Reels: 3 s–15 min, ≤300 MB; 100 API posts/24 h per docs (content_publishing_limit reports 50).
- Same pattern for each: own uploader module, `<P>_ENABLED`, state file, Telegram line, `/<p>fill`, hidden test.

## X target (code 2026-11-19, runbook §7)

- `src/x_uploader.py`: API v2 `/2/media/upload/initialize|append|finalize` + STATUS poll, `/2/tweets`,
  OAuth 1.0a via `requests-oauthlib`. Text = title only (URLs stripped: a link costs $0.20 vs ~$0.015).
  401/402/403 → `XAuthError` (stops X for the run; 402 = credit used up), 429 → `XLimit`; a
  "duplicate content" 403 retries once with the upload date appended. Post creation never blind-retries.
- Instagram and X share `_post_shorts(key, ...)` / `run_shorts_fill` in `pipeline.py` (targets in
  `_shorts_targets()`); Telegram `/igfill` and `/xfill` share `_shorts_fill`.
- Setup gotchas: the console rejects `https://localhost` as callback ("Not a valid callback URL format");
  the console's own Access Token is for the developer-account owner, so a different posting account needs
  the OAuth 1.0a PIN flow opened in a private window logged in as that account. Don't create a project
  under the posting account (it opens a second, empty developer account). Never commit handles or IDs.
- Go-live (2026-11-19): a private helper patches the Secret with the 4 keys and `X_ENABLED=false`, runs
  `check` (prints the posting @handle) + `check --upload` (media processed, not posted), then asks before
  `X_ENABLED=true` + restart. Keep the helpers outside the repo (they hold the jumpbox address).
- After any restart, the first `check_new` runs ~1 min later and holds the relay lock for ~1 min per
  Short; a `/xfill` or `/igfill` sent then gets "A relay is already running". Not a bug — wait for its report.
- Cost: ~$0.015 per post → 3/day ≈ $1.35/month. 402 from X = credit used up (shows in Telegram).

## TikTok target (2026-11-21, runbook §8)

- `src/tiktok_uploader.py`: Content Posting API, Direct Post, `FILE_UPLOAD`. `creator_info` before every post
  (privacy options, `max_video_post_duration_sec`), `video/init` (no blind retry), PUT chunks, poll
  `status/fetch` (still processing after 10 min → counted as posted, to avoid double posts).
- **Chunk rule (bug hit live):** `chunk_size` may never exceed the file. Files ≤64 MB go as ONE chunk
  (`chunk_size = size`); bigger: 10 MB chunks, last absorbs the remainder. A 5–10 MB file with a 10 MB
  chunk gave `invalid_params: The chunk size is invalid`.
- OAuth 2: access token 24 h, refresh token 365 days, **the bot refreshes and rewrites
  `/data/tiktok_token.json`** (refresh token may rotate) — keep it on the volume, not in the Secret.
  Seed: `python -m src.tiktok_uploader seed < tiktok_token.json`; `check` prints @handle, privacy options, max length.
- Portal setup: app type **Other**; Terms/Privacy/Web URL on the Pages site, verified via **URL properties →
  URL prefix** (download the `tiktok….txt` signature, publish it on `gh-pages`, then Verify). Production can't be
  saved without a demo video → build everything in a **Sandbox** first (clone from production). Login Kit
  redirect URI `…/tiktok-callback.html` (Pages page that shows `?code=`); **pasted URI gave "Invalid Redirect
  Uri", typing it by hand worked**. Content Posting API: Direct Post ON; "Verify domains" only for PULL_FROM_URL.
  `video.upload` is auto-added with the product. Add the posting account under Sandbox → Target Users.
- **Unaudited app rule:** `unaudited_client_can_only_post_to_private_accounts` — the whole TikTok ACCOUNT must
  be private, `SELF_ONLY` alone is not enough (even though creator_info lists PUBLIC_TO_EVERYONE). The bot maps
  it to a plain message and **pauses automatic TikTok attempts 12 h** (each attempt downloads a Short first);
  `/ttfill` ignores the pause. Test + demo recording = flip the account private for ~15 min, then back.
- Public posting only after TikTok's app review: then production client key/secret, re-login,
  `TIKTOK_PRIVACY=PUBLIC_TO_EVERYONE`. Risk: TikTok's UX guidelines expect per-post privacy choice in a UI;
  fallback = `video.upload` (drafts to the creator's inbox).
- Private helpers outside the repo: `tiktok-auth.py` (prints authorize URL, exchanges pasted code, checks scopes)
  and `tiktok-deploy.py` (Secret with `TIKTOK_ENABLED=false`, seed, check, ask before enabling).

## Telegram, stats, health, captions, tests (2026-11-21/22)

- Messages: icon per platform (▶️ 📘 📸 ✖️ 🎵), `━━━` dividers, ✅/⚠️/⏳ headers, link previews off
  (`Defaults(link_preview_options=LinkPreviewOptions(is_disabled=True))`). Scheduled reports are headed
  "Scheduled check". `/status` = last scheduled run (in memory, empty after restart); `/stats [days]` = history.
- `src/stats_db.py`: SQLite `/data/relay.db` (`posts`, `runs`, `meta`); best-effort writes, one-time import
  of the state files' per-day counters (`source='import'`). Shorts uploaders set `LAST_ERROR` for the reason.
- `src/health.py`: daily 10:00 reminder ≤14 days before expiry of yt-dlp Google login cookies (earliest auth
  cookie), Facebook token (`debug_token` with the Page token itself: `expires_at` 0 = never,
  `data_access_expires_at`), TikTok refresh token; listed in `/stats`. Watchdog every 30 min: silent 6 h,
  stuck 90 min, last 3 runs aborted (quota stops excluded); alert once + "recovered".
  `HEALTHCHECK_URL` (healthchecks.io, 2 h period + 1 h grace, Telegram integration) pinged after each run,
  `/fail` on abort. Use the **uuid** ping URL, not the slug one (needs a ping key). Its "TEST is DOWN"
  message is the integration's test button, not a real check.
- `src/captions.py`: Arabic credit `المصدر: <channel>` (+ link on YouTube/Facebook only), hashtags from
  `CAPTION_HASHTAGS` — 3 YT/FB, 5 IG/TikTok, 2 X; no links on X (cost) or IG/TikTok (not clickable).
  YouTube descriptions can't contain `<`/`>`.
- Tests: `tests/` (pytest, mocked HTTP, env set in `conftest.py` before `src.config` import). CI
  `.github/workflows/tests.yml` is called by build-push-dockerhub (`needs: tests`), so a failing test blocks the image.
- Found `X_ENABLED=false` in the Secret with no record of who set it: check flags in the Secret before blaming code.

## Fixes and Facebook Stories (2026-11-25/26)

- **YouTube player change breaks every download at once.** Symptom: `[jsc] Error solving n challenge … a.addOnDisposeCallback
  is not a function`, then `Only images are available` / `Requested format is not available` on every platform (and
  `Metadata fetch failed` for Shorts). Fix = newer yt-dlp: search `gh search issues "<error text>" --repo yt-dlp/yt-dlp`,
  find the fixed release, raise the floor in `requirements.txt` (e.g. `yt-dlp[default]>=2026.11.23`) — bumping the pin
  also forces a fresh pip layer — push, rollout restart. Unprocessed videos are retried automatically.
- **Instagram 1080p Shorts (17–29 MB) fail** with rupload `400 ProcessingFailedError` (`retriable:false`) while 4–5 MB
  files post fine; the same file sometimes posts on a later try. When a container finishes after that 400 it does so
  within ~30 s. Fix (`f694412`): Reel copy re-encoded to `IG_MAX_WIDTH` 720 with bitrate capped near `IG_TARGET_MB` 12;
  after the 400 wait `FAILED_UPLOAD_WAIT` 2 min, then one retry with a new container; per-Short `fails` counter in
  `instagram_state.json`, skipped after `IG_MAX_FAILS` 3 runs. Confirmed: a Short that failed 6+ times posted first try.
  The normal-path container wait is still 15 min (shorten only if `IN_PROGRESS` stalls return).
- **Facebook Stories target** (`src/fb_story_uploader.py`, key `fbs`, platform `fb_story`, icon 📖, `/fbsfill`):
  `POST /{page}/video_stories` start → rupload (same as Reels) → finish (`post_id`); same Page token, no new scope.
  Meta allows 3–60 s per Story and the source Shorts run 66–173 s, so a Short is cut into equal parts
  (`FBS_SEGMENT_SECONDS` 60, ffmpeg `-ss/-t` + re-encode, letterboxed 1080x1920) posted back to back;
  `FBS_MAX_SECONDS` 180; the daily cap (`FBS_DAILY_LIMIT` 1 during the FB warm-up) counts Shorts, not parts. A later
  part failing keeps the Short as posted (no reposts). Old "too long" skips that now fit are dropped on load.
  Confirmed 2026-11-26: a Short posted as 2 Story parts.
- Shorts runner: a too-long skip used to be silent (4 fetches and the run ended with no reason in the log); it now logs.
- **TikTok app review submitted 2026-11-26** (Production → In review; don't press Recall). The review form has ONE
  1000-char box for all products/scopes and a 120-char "reason" box. Keep the review text consistent with the
  Pages site (videos come from a YouTube channel with the creator's permission and credit, not "the owner's own
  channel"). Next steps on approval/rejection are in `open-items.md`.
- Killing the pod with `rollout restart` mid-run shows the old pod as `Error`; harmless (videos are only marked
  processed after upload). Running `git commit` while Claude commits gives `index.lock: File exists`; just re-check.

## Deploy commands

```bash
kubectl create ns relay-bot
kubectl -n relay-bot create secret docker-registry dockerhub-pull \
  --docker-server=https://index.docker.io/v1/ --docker-username=ibn3bbas \
  --docker-password=<read-only token>
kubectl -n relay-bot create secret generic relay-bot-env --from-env-file=.env
kubectl -n relay-bot create secret generic relay-bot-files \
  --from-file=token.json=config/token.json \
  --from-file=client_secret.json=config/client_secret.json
kubectl apply -f k8s/relay-bot.yaml
kubectl -n relay-bot logs -f deploy/relay-bot
```

## Day 2

```bash
kubectl -n relay-bot rollout restart deploy/relay-bot          # pick up new :latest
kubectl -n relay-bot exec deploy/relay-bot -- cat /data/processed_videos.json
POD=$(kubectl -n relay-bot get pod -l app=relay-bot -o name | cut -d/ -f2)
kubectl -n relay-bot cp config/token.json "$POD":/data/token.json   # after local re-auth
```
