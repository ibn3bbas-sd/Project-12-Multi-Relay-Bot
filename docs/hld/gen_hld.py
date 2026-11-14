#!/usr/bin/env python3
"""Generate docs/hld/hld-relay-bot-cce.svg — theme-aware (light/dark) FHD HLD.

    python3 docs/hld/gen_hld.py

Stdlib only. Edit the content here, never the SVG by hand.
"""
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1920, 1080
FONT = "'Inter','Segoe UI','Noto Sans','DejaVu Sans',Helvetica,Arial,sans-serif"

THEMES = {
    "light": dict(bg="#ffffff", dot="#eef1f4", text="#1f2328", muted="#57606a", faint="#8c959f",
                  border="#d0d7de", panel="#f6f8fa", card="#ffffff", blue="#0969da", orange="#d4600a",
                  green="#1a7f37", purple="#8250df", red="#cf222e", grey="#6e7781", yt="#e5252a",
                  tg="#229ed9", tint=0.07),
    "dark": dict(bg="#0d1117", dot="#161b22", text="#e6edf3", muted="#9198a1", faint="#6e7681",
                 border="#30363d", panel="#161b22", card="#0d1117", blue="#4493f8", orange="#f0883e",
                 green="#3fb950", purple="#a371f7", red="#f85149", grey="#8b949e", yt="#ff4d4f",
                 tg="#3bb4ec", tint=0.12),
}

# 24x24 line glyphs, drawn white on a coloured tile
GLYPHS = {
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>',
    "send": '<path d="M21 3 10.5 13.5M21 3l-6.5 18-4-7.5L3 9.5z"/>',
    "play": '<rect x="2.5" y="5" width="19" height="14" rx="4"/><path d="M10 9v6l5-3z" fill="#fff"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
    "git": '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="7" r="2.2"/>'
           '<path d="M6 7.2v9.6M18 9.2c0 4.5-6 4-11 8"/>',
    "box": '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
    "key": '<circle cx="7.5" cy="15.5" r="4"/><path d="M10.5 12.5 20 3M16.5 6.5l3 3"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/>'
           '<path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.5a4 4 0 0 1 8 0V11"/>',
    "disk": '<ellipse cx="12" cy="6" rx="8" ry="2.6"/><path d="M4 6v12c0 1.5 3.6 2.6 8 2.6s8-1.1 8-2.6V6'
            'M4 12c0 1.5 3.6 2.6 8 2.6s8-1.1 8-2.6"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "nat": '<path d="M4 8h15M15 4l4 4-4 4M20 16H5M9 12l-4 4 4 4"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "ban": '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    "dns": '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/>'
           '<path d="M7 7h.01M7 17h.01"/>',
    "seed": '<path d="M12 21v-9M12 12C12 7 8 4 4 4c0 5 3 8 8 8zM12 14c0-4 3-7 8-7 0 4-3 7-8 7z"/>',
}


def build(t):
    o = []
    a = o.append
    pills = []

    def text(x, y, s, size=13, weight=400, fill="text", anchor="start", ls=0):
        a(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
          f'fill="{t.get(fill, fill)}" text-anchor="{anchor}" letter-spacing="{ls}">{escape(s)}</text>')

    def rect(x, y, w, h, fill="none", stroke="border", sw=1.4, rx=12, dash=None, op=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        fo = f' fill-opacity="{op}"' if op is not None else ""
        a(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" fill="{t.get(fill, fill)}"{fo} '
          f'stroke="{t.get(stroke, stroke)}" stroke-width="{sw}"{d}/>')

    def icon(x, y, name, color, size=52):
        rect(x, y, size, size, fill=color, stroke=color, sw=1, rx=size * 0.22)
        s = size / 24 * 0.62
        off = size * 0.19
        a(f'<g transform="translate({x + off},{y + off}) scale({s:.3f})" fill="none" stroke="#fff" '
          f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{GLYPHS[name]}</g>')

    def card(x, y, w, h, ico, color, title, sub, foot=None):
        rect(x, y, w, h, fill=color, stroke=color, sw=1.6, rx=10, op=t["tint"])
        rect(x, y, w, h, fill="none", stroke=color, sw=1.6, rx=10)
        icon(x + 14, y + 14, ico, color, 34)
        text(x + 60, y + 30, title, 14.5, 700)
        text(x + 60, y + 47, sub, 11.5, 400, "muted")
        if foot:
            text(x + 16, y + h - 14, foot, 11, 400, "faint")

    def pill(x, y, w, label, color):
        pills.append((x, y, w, label, color))

    def _pill(x, y, w, label, color):
        rect(x, y, w, 32, fill="card", stroke=color, sw=1.8, rx=8)
        a(f'<circle cx="{x + 18}" cy="{y + 16}" r="5.5" fill="{t[color]}"/>')
        text(x + 32, y + 21.5, label, 14.5, 700, color)

    def line(pts, color, dash=None, arrow_end=True, arrow_start=False, sw=1.8):
        d = " ".join(f"{'M' if i == 0 else 'L'}{px} {py}" for i, (px, py) in enumerate(pts))
        da = f' stroke-dasharray="{dash}"' if dash else ""
        me = f' marker-end="url(#ah-{color}-{THEME})"' if arrow_end else ""
        ms = f' marker-start="url(#ah-{color}-{THEME})"' if arrow_start else ""
        a(f'<path d="{d}" fill="none" stroke="{t[color]}" stroke-width="{sw}"{da}{me}{ms}/>')

    def bullets(x, y, items, color="blue", gap=31):
        for i, (l1, l2) in enumerate(items):
            yy = y + i * gap
            a(f'<circle cx="{x + 2}" cy="{yy - 4}" r="2.8" fill="{t[color]}"/>')
            text(x + 14, yy, l1, 11.8, 400)
            if l2:
                text(x + 14, yy + 14, l2, 11.8, 400, "muted")

    # markers
    a("<defs>")
    for c in ("blue", "orange", "green", "purple", "red", "grey", "tg", "yt"):
        a(f'<marker id="ah-{c}-{THEME}" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" '
          f'orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="{t[c]}"/></marker>')
    a("</defs>")

    # background + header
    rect(0, 0, W, H, fill="bg", stroke="bg", sw=0, rx=0)
    a(f'<rect width="{W}" height="{H}" fill="url(#dots-{THEME})"/>')
    text(48, 52, "YouTube Relay Bot — High-Level Design (HLD)", 30, 700)
    text(48, 78, "Source channel → yt-dlp → YouTube Data API v3 → target channel · Huawei Cloud CCE me-east-1 (Riyadh)"
                 " · ns relay-bot · 1 pod, Recreate · EVS volume · egress-only via NAT + EIP · Telegram control",
         15.5, 400, "muted")
    text(1880, 52, "FHD 1920 × 1080", 14, 600, "faint", "end")
    text(1880, 74, "v1 · theme-aware", 13.5, 400, "faint", "end")
    a(f'<line x1="48" y1="96" x2="1880" y2="96" stroke="{t["border"]}" stroke-width="1.5"/>')

    # ── left column ────────────────────────────────────────────────────────
    rect(48, 120, 250, 412, fill="panel")
    text(66, 152, "At a glance", 16.5, 700)
    bullets(68, 176, [
        ("Region me-east-1 (Riyadh), one", "CCE cluster, namespace relay-bot"),
        ("Exactly 1 pod, Recreate rollout —", "two pods = every video twice"),
        ("Egress only: no Service, Ingress,", "ELB or public IP on the node"),
        ("Telegram via long-polling, so", "no inbound webhook is needed"),
        ("State on one EVS volume: token,", "processed IDs, download scratch"),
        ("OAuth done once on a laptop;", "token seeded through a Secret"),
        ("Image: GitHub Actions → SWR,", "linux/amd64, :latest + :<sha>"),
        ("NetworkPolicy: deny ingress,", "egress DNS 53 + HTTPS 443 only"),
        ("uid 10001 · read-only root FS ·", "all capabilities dropped"),
        ("Secrets never in git or image;", "SWR AK only in GitHub secrets"),
        ("Next: Arabic titles via Gemini", "(config keys exist, not wired)"),
    ], gap=32)

    rect(48, 552, 250, 216, fill="panel")
    text(66, 582, "Schedule & quota", 16.5, 700)
    text(66, 602, "the real ceiling is YouTube, not CPU", 12, 400, "muted")
    icon(66, 616, "clock", "blue", 30)
    text(106, 629, "06:00 · 18:00 Asia/Riyadh", 12.5, 700)
    text(106, 644, "APScheduler via job_queue", 11.5, 400, "muted")
    # quota bar: 10,000 units, ~1,600 per insert
    bx, by, bw = 66, 662, 214
    rect(bx, by, bw, 14, fill="border", stroke="border", sw=0, rx=3)
    seg = bw * 1600 / 10000
    for i in range(6):
        rect(bx + i * seg + 1, by + 1, seg - 2, 12, fill=["red", "orange"][i % 2], stroke="bg", sw=0, rx=2)
    text(66, 694, "10,000 units/day ÷ ~1,600 per insert", 11.5, 400)
    text(66, 709, "≈ 6 uploads per day, hard limit", 11.5, 700, "red")
    text(66, 728, "max_uploads_per_run 5 · scan_limit 20", 11.5, 400, "muted")
    text(66, 743, "max_age_days 7 · 1080p · 8 MB chunks", 11.5, 400, "muted")
    text(66, 760, "Quota spent → run aborts, reports, no retry", 11, 400, "faint")

    rect(48, 788, 250, 234, fill="panel")
    text(66, 818, "Durability", 16.5, 700)
    text(66, 838, "what survives what", 12, 400, "muted")
    rows = [
        ("Pod crash → restart", "green", "same PVC, same token, same IDs"),
        ("Node loss → reschedule", "green", "EVS is zonal: pod follows its AZ"),
        ("Failed download → retried", "orange", "not marked processed until done"),
        ("Token revoked → re-auth", "red", "laptop OAuth, kubectl cp to /data"),
    ]
    for i, (h, c, s) in enumerate(rows):
        yy = 862 + i * 36
        text(66, yy, h, 12.5, 700, c)
        text(66, yy + 16, s, 11.3, 400, "muted")
    text(66, 1010, "No HA by design · volume = single copy", 11, 400, "faint")

    # ── centre: context note + actors ───────────────────────────────────────
    rect(1150, 116, 266, 150, fill="card")
    text(1166, 140, "One pod that only dials out", 13.5, 700)
    for i, s in enumerate(["Twice a day (or on /run) the pod lists",
                           "the source channel, downloads new",
                           "videos with yt-dlp and re-uploads them",
                           "with the YouTube API. Nothing connects",
                           "in: Telegram is polled, every call",
                           "leaves through the NAT Gateway EIP."]):
        text(1166, 162 + i * 16, s, 11.3, 400, "muted")

    actors = [
        (470, "git", "purple", "GitHub · main", "Actions build-push-swr"),
        (630, "user", "tg", "Owner", "Telegram app · 1 chat ID"),
        (770, "send", "tg", "Telegram Bot API", "api.telegram.org"),
        (910, "play", "yt", "Source channel", "youtube.com · yt-dlp"),
        (1050, "upload", "yt", "YouTube Data API", "v3 → target channel"),
    ]
    for cx, ico, col, title, sub in actors:
        icon(cx - 26, 140, ico, col)
        text(cx, 214, title, 14.5, 700, "text", "middle")
        text(cx, 232, sub, 11.8, 400, "muted", "middle")
    line([(658, 166), (742, 166)], "tg", arrow_start=True)
    text(700, 158, "/run /status", 10.5, 600, "tg", "middle")

    # ── region ─────────────────────────────────────────────────────────────
    rect(320, 300, 1112, 722, stroke="red", sw=1.8, rx=14, dash="9 7")
    pill(336, 284, 330, "Huawei Cloud · me-east-1 (Riyadh)", "red")

    card(360, 340, 220, 62, "box", "purple", "SWR", "org/youtube-relay-bot")
    card(1150, 340, 250, 62, "key", "orange", "IAM · AK + login key", "SWR push creds (GitHub only)")

    # VPC
    rect(344, 424, 1064, 584, stroke="orange", sw=1.6, rx=12, dash="7 6")
    pill(360, 408, 300, "VPC · CCE cluster network", "orange")

    # node subnet
    rect(368, 466, 1016, 404, stroke="blue", sw=1.5, rx=12, dash="4 5")
    pill(384, 450, 280, "node subnet · private · CCE", "blue")

    # namespace
    rect(392, 506, 968, 352, fill="blue", stroke="purple", sw=1.4, rx=12, dash="2 4", op=0.03)
    pill(408, 490, 260, "namespace · relay-bot", "purple")

    # left column inside ns
    card(416, 540, 236, 70, "seed", "green", "init: seed-token",
         "busybox · runs before the bot")
    text(432, 626, "cp token.json → /data only if missing", 10.8, 400, "faint")
    card(416, 640, 236, 62, "lock", "orange", "Secret relay-bot-env", "TELEGRAM_* · channels · Gemini")
    card(416, 716, 236, 62, "lock", "orange", "Secret relay-bot-files", "token.json · client_secret.json")
    text(432, 800, "kubectl create secret … from .env", 10.8, 400, "faint")
    text(432, 815, "and config/ — never committed", 10.8, 400, "faint")

    # pod
    px, py, pw, ph = 700, 540, 424, 220
    rect(px, py, pw, ph, fill="blue", stroke="blue", sw=1.8, rx=12, op=t["tint"])
    rect(px, py, pw, ph, stroke="blue", sw=1.8, rx=12)
    icon(px + 16, py + 16, "cpu", "blue", 40)
    text(px + 68, py + 36, "Deployment relay-bot · 1 pod", 15.5, 700)
    text(px + 68, py + 53, "python 3.12-slim + ffmpeg · python -m src.main", 11.5, 400, "muted")
    mods = [
        ("tg", "telegram_bot", "/run /status /next /help + notifications"),
        ("blue", "scheduler", "job_queue → scheduled_relay 06:00 / 18:00"),
        ("yt", "pipeline", "_relay_lock · asyncio.to_thread"),
        ("yt", "  downloader", "yt-dlp: flat listing → per-video metadata"),
        ("yt", "  uploader", "resumable insert · QuotaExceeded → abort"),
    ]
    for i, (c, m, d) in enumerate(mods):
        yy = py + 88 + i * 21
        a(f'<rect x="{px + 18}" y="{yy - 10}" width="4" height="13" rx="1" fill="{t[c]}"/>')
        text(px + 30, yy, m, 12, 700)
        text(px + 128, yy, d, 11.5, 400, "muted")
    text(px + 18, py + ph - 12, "replicas 1 · Recreate · req 250m/512Mi · lim 1 CPU/1Gi", 11, 400, "faint")

    # PVC
    card(700, 780, 276, 62, "disk", "green", "PVC relay-data → /data", "EVS 20 Gi SAS · csi-disk · RWO")
    text(988, 800, "token.json", 11, 600, "green")
    text(988, 815, "processed_videos.json", 11, 600, "green")
    text(988, 830, "downloads/ scratch", 11, 400, "muted")

    # right column inside ns
    card(1150, 540, 194, 70, "shield", "red", "NetworkPolicy", "ingress: none")
    text(1166, 626, "egress: UDP/TCP 53, TCP 443", 10.8, 400, "faint")
    card(1150, 640, 194, 62, "ban", "grey", "No Service", "no Ingress · no ELB")
    text(1166, 726, "nothing listens; Telegram", 10.8, 400, "faint")
    text(1166, 741, "is polled, not webhooked", 10.8, 400, "faint")

    # gateways
    text(392, 918, "GATEWAYS", 12, 700, "faint", ls=1.2)
    text(392, 936, "egress path", 11, 400, "faint")
    card(520, 896, 260, 62, "dns", "grey", "CoreDNS", "in-cluster · port 53")
    card(820, 896, 300, 62, "nat", "grey", "NAT Gateway · SNAT", "node subnet → EIP · fixed egress IP")
    card(1150, 896, 230, 62, "globe", "grey", "EIP", "bound to the NAT rule")

    # ── flows ──────────────────────────────────────────────────────────────
    # build: GitHub → SWR → pod
    line([(470, 244), (470, 340)], "purple")
    text(478, 282, "docker push", 11, 700, "purple")
    line([(580, 371), (684, 371), (684, 612), (700, 612)], "purple", dash="6 5")
    text(690, 514, "image pull", 11, 700, "purple")
    text(690, 528, "default-secret", 10.5, 400, "purple")
    # egress from pod up to the internet services
    for x, col, lab in [(770, "tg", "long-poll 443"), (910, "yt", "download 443"), (1050, "yt", "insert 443")]:
        line([(x, py), (x, 244)], col)
        text(x + 6, 282, lab, 11, 700, col)
    # state
    line([(652, 575), (700, 575)], "green", dash="5 4")
    line([(534, 702), (534, 716)], "orange", arrow_end=False)
    line([(652, 668), (700, 668)], "orange", dash="5 4")
    text(656, 662, "env", 10, 700, "orange")
    line([(860, py + ph), (860, 780)], "green", arrow_start=True)
    text(866, 774, "mount", 10.5, 700, "green")
    # pod → NAT
    line([(1116, py + ph), (1116, 896)], "grey", dash="3 4")
    text(1010, 880, "all egress", 10.5, 700, "grey")

    for pp in pills:
        _pill(*pp)

    # ── right column: abbreviations ─────────────────────────────────────────
    rect(1466, 120, 414, 902, fill="panel")
    text(1484, 152, "Abbreviations", 16.5, 700)
    text(1484, 172, "every acronym used in this diagram", 12, 400, "muted")
    groups = [
        ("HUAWEI CLOUD", "blue", [("CCE", "Cloud Container Engine (Kubernetes)"),
                                  ("SWR", "SoftWare Repository for Container"),
                                  ("EVS", "Elastic Volume Service (block disk)"),
                                  ("VPC", "Virtual Private Cloud"),
                                  ("NAT", "Network Address Translation"),
                                  ("SNAT", "Source NAT (outbound only)"),
                                  ("EIP", "Elastic IP"),
                                  ("IAM", "Identity & Access Management"),
                                  ("AK", "Access Key (paired with a login key)")]),
        ("KUBERNETES", "purple", [("ns", "Namespace"),
                                  ("PVC", "PersistentVolumeClaim"),
                                  ("RWO", "ReadWriteOnce — one node mounts it"),
                                  ("DNS", "CoreDNS service discovery")]),
        ("APPLICATION", "red", [("API v3", "YouTube Data API, version 3"),
                                ("OAuth", "Google consent → refresh token"),
                                ("yt-dlp", "YouTube downloader (Python)"),
                                ("HLD", "High-Level Design")]),
    ]
    y = 204
    for g, c, items in groups:
        text(1490, y, g, 12, 700, "faint", ls=1.2)
        y += 24
        for k, v in items:
            a(f'<rect x="1490" y="{y - 12}" width="3" height="15" fill="{t[c]}"/>')
            text(1504, y, k, 13.5, 700)
            text(1580, y, v, 12.5, 400, "muted")
            y += 23
        y += 14
    text(1490, y, "FLOW KEY", 12, 700, "faint", ls=1.2)
    y += 24
    for c, dash, lab in [("purple", None, "Build — GitHub Actions → SWR → pull"),
                         ("tg", None, "Control — Telegram long-poll"),
                         ("yt", None, "Data — yt-dlp in · API v3 out"),
                         ("green", None, "State — EVS volume / token seed"),
                         ("orange", "5 4", "Config — Secrets → env + files"),
                         ("grey", "3 4", "Egress — every call via NAT + EIP")]:
        line([(1500, y - 4), (1552, y - 4)], c, dash=dash)
        text(1580, y, lab, 12.5, 400, "muted")
        y += 24

    # footer
    text(48, 1048, "push to main → Actions → SWR :latest/:sha → rollout restart   ·   /run or 06:00/18:00 → yt-dlp"
                   " → /data/downloads → videos.insert → target channel → Telegram report   ·   state: /data on EVS",
         13, 400, "muted")
    text(48, 1068, "Never scale past 1 replica   ·   cloud IPs can hit YouTube's bot-check: smoke-test yt-dlp from the"
                   " cluster first   ·   OAuth only on a laptop   ·   secrets never in git or the image",
         13, 700, "muted")
    return "\n".join(o)


parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
         'aria-label="YouTube Relay Bot high-level design on Huawei Cloud CCE">',
         "<style>.theme-dark{display:none}@media (prefers-color-scheme: dark){"
         ".theme-light{display:none}.theme-dark{display:inline}}</style>"]
for THEME, t in THEMES.items():
    parts.append(f'<defs><pattern id="dots-{THEME}" width="32" height="32" patternUnits="userSpaceOnUse">'
                 f'<circle cx="1.2" cy="1.2" r="1.2" fill="{t["dot"]}"/></pattern></defs>')
    parts.append(f'<g class="theme-{THEME}">')
    parts.append(build(t))
    parts.append("</g>")
parts.append("</svg>")

out = Path(__file__).with_name("hld-relay-bot-cce.svg")
out.write_text("\n".join(parts) + "\n", encoding="utf-8")
print(f"wrote {out}")
