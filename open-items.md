# Open items

Where the Huawei Cloud CCE deployment stands. Last updated **2026-11-17**.
Runbook: [docs/deploy-cce.md](docs/deploy-cce.md) · Diagram: [docs/hld/hld-relay-bot-cce.svg](docs/hld/hld-relay-bot-cce.svg)

> This repo is **private** since 2026-11-17, but keep it clean: Never write tokens, IPs, hostnames, SSH users/keys or account names here.
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

## Still open

- [ ] Delete the old, retained EVS disk left by the first PVC (Retain policy) in the console
- [x] Bot-check failures now count as Failed in the Telegram report, with a "cookies likely expired" hint
- [x] `kubeconform -strict -summary k8s/relay-bot.yaml`: 4 valid, 0 invalid (2026-11-17)

## Cleanup

- [x] Unused GitHub secrets `SWR_REGISTRY_USER` / `SWR_REGISTRY_PASS` deleted
- [ ] Delete the old `github-actions-push` Docker Hub token that had *Public Read-only* scope (if not already)
- [ ] Review the auto-generated Docker Hub "desktop" tokens (Read/Write/Delete, never expire) and revoke unused ones
- [x] Empty the exported cookies file from the OneDrive recycle bin (2026-11-18)
- [x] Infrastructure details in git history: rewritten 2026-11-18 (see below)
- [ ] GitHub Pages (home + privacy) went offline when the repo went private; host them elsewhere if Google needs the privacy URL

## Git history cleanup (2026-11-18)

History was rewritten with `git filter-repo` (infra IPs, host/cluster/key/repo names replaced with placeholders;
commit email mapped to the GitHub noreply address) and force-pushed. No passwords, tokens or keys were ever committed.

- [x] Rewrite + force-push `main` and `gh-pages`; local clone reset and old objects purged
- [ ] Ask GitHub Support to purge cached views and PR refs (#1, #2 still point at old commits) and run GC
- [x] Check forks: none (2026-11-18)
- [x] ~~Change the jumpbox public IP~~ skipped: port 22 is allow-listed, SSH is key-only with fail2ban, so a known IP gives no access
- [ ] Make the repo public, re-enable Pages on `gh-pages`
