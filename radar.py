import os
import re
import json
import math
import html
import datetime as dt
import collections
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
KAKAO_REST_API_KEY = os.environ["KAKAO_REST_API_KEY"]
KAKAO_REFRESH_TOKEN = os.environ["KAKAO_REFRESH_TOKEN"]
KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")

# Optional. Event Insight is the ONLY LLM step.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "").strip()

GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "").strip()
KST = ZoneInfo("Asia/Seoul")

SEARCH_QUERIES = [
    '"Galaxy Unpacked" keynote',
    '"Apple Event" | WWDC | "Made by Google"',
    '"NVIDIA GTC" | "Snapdragon Summit" | "Microsoft Build"',
    '"tech keynote" | "product launch event" | "stage production"',
    'SXSW | Coachella | "Netflix event" | "Disney D23"',
]

OFFICIAL_CHANNELS = {
    "samsung", "samsung mobile", "apple", "google", "made by google",
    "google for developers", "microsoft", "nvidia", "qualcomm",
    "snapdragon", "meta", "amazon web services", "aws events",
    "adobe", "netflix", "disney", "sxsw", "coachella",
}

TRUSTED_CHANNELS = {
    "the verge", "cnet", "engadget", "wired", "techcrunch",
    "bloomberg technology", "cnbc", "cnbc international", "reuters",
    "bbc news", "financial times", "euronews next",
    "연합뉴스", "yonhapnews", "매일경제", "한국경제tv", "전자신문",
    "zdnet korea", "디지털데일리", "블로터",
}

TRUSTED_NEWS_SOURCE_HINTS = {
    "samsung", "apple", "google", "microsoft", "nvidia", "qualcomm",
    "reuters", "bloomberg", "cnbc", "the verge", "techcrunch", "wired",
    "financial times", "bbc", "cnet", "engadget",
    "연합뉴스", "매일경제", "한국경제", "전자신문",
    "zdnet korea", "디지털데일리", "블로터",
}

EXCLUDE_TERMS = [
    "#shorts", "shorts", "reaction", "rumor", "rumour", "leak",
    "review", "unboxing", "podcast", "tutorial", "hands-on", "hands on",
    "vlog", "price", "comparison", "fan edit", "my thoughts",
]

KNOWN_EVENTS = [
    ("Galaxy Unpacked", ["galaxy unpacked"]),
    ("Apple Event", ["apple event"]),
    ("WWDC", ["wwdc"]),
    ("Made by Google", ["made by google"]),
    ("Google I/O", ["google i/o"]),
    ("Microsoft Build", ["microsoft build"]),
    ("Microsoft Ignite", ["microsoft ignite"]),
    ("NVIDIA GTC", ["nvidia gtc", "gtc keynote"]),
    ("Snapdragon Summit", ["snapdragon summit"]),
    ("Meta Connect", ["meta connect"]),
    ("AWS re:Invent", ["aws re:invent", "re:invent"]),
    ("Adobe MAX", ["adobe max"]),
    ("SXSW", ["sxsw"]),
    ("Coachella", ["coachella"]),
    ("Netflix Event", ["netflix event", "netflix experience"]),
    ("Disney D23", ["disney d23", "d23"]),
]

STOPWORDS = {
    "the","and","for","this","that","with","from","have","has","was","were",
    "you","your","our","are","but","not","all","new","more","just","about",
    "into","out","its","they","their","what","when","where","how",
    "영상","행사","공개","관련","이번","통해","대한","있는","있다","에서","으로",
}

def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def duration_seconds(iso):
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, sec = [int(x or 0) for x in m.groups()]
    return h * 3600 + mi * 60 + sec

def yt_get(path, params):
    p = dict(params)
    p["key"] = YOUTUBE_API_KEY
    r = requests.get(
        f"https://www.googleapis.com/youtube/v3/{path}",
        params=p, timeout=30
    )
    r.raise_for_status()
    return r.json()

def source_tier(channel):
    c = norm(channel)
    if c in OFFICIAL_CHANNELS:
        return 2
    if c in TRUSTED_CHANNELS:
        return 1
    return 0

def canonical_event(title, channel):
    t = norm(title)
    for event_name, aliases in KNOWN_EVENTS:
        if any(alias in t for alias in aliases):
            return event_name

    for phrase in (
        "keynote", "launch event", "summit", "conference",
        "stage production", "brand activation", "immersive experience",
    ):
        if phrase in t:
            return f"{channel} — {phrase.title()}"

    words = [
        w for w in re.findall(r"[a-z0-9가-힣]+", t)
        if len(w) > 2 and w not in STOPWORDS
    ][:6]
    return " ".join(words) or title[:50]

def fetch_youtube_candidates():
    after = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
    found = {}

    for query in SEARCH_QUERIES:
        data = yt_get("search", {
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "date",
            "publishedAfter": after,
            "maxResults": 10,
            "safeSearch": "moderate",
        })

        ids = [
            x["id"]["videoId"] for x in data.get("items", [])
            if x.get("id", {}).get("videoId")
        ]
        if not ids:
            continue

        detail = yt_get("videos", {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(ids),
        })

        for v in detail.get("items", []):
            sn = v["snippet"]
            title = sn.get("title", "")
            channel = sn.get("channelTitle", "")
            description = sn.get("description", "")
            low = norm(title)
            duration = duration_seconds(v.get("contentDetails", {}).get("duration", ""))
            views = int(v.get("statistics", {}).get("viewCount", 0))

            if duration and duration <= 180:
                continue
            if any(term in low for term in EXCLUDE_TERMS):
                continue

            tier = source_tier(channel)
            if tier == 0:
                continue

            vid = v["id"]
            found[vid] = {
                "id": vid,
                "title": title,
                "channel": channel,
                "description": description[:1600],
                "published": sn.get("publishedAt", ""),
                "views": views,
                "tier": tier,
                "event": canonical_event(title, channel),
                "url": f"https://www.youtube.com/watch?v={vid}",
                "thumbnail": sn.get("thumbnails", {}).get("high", {}).get(
                    "url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                ),
            }

    return list(found.values())

def score_video(v):
    score = 100 if v["tier"] == 2 else 45
    try:
        pub = dt.datetime.fromisoformat(v["published"].replace("Z", "+00:00"))
        age_h = (dt.datetime.now(dt.timezone.utc) - pub).total_seconds() / 3600
        score += 12 if age_h <= 24 else 8 if age_h <= 72 else 4
    except Exception:
        pass

    score += min(7, math.log10(max(v["views"], 1)))
    return score

def choose_distinct_events(items, n=3):
    best = {}
    for v in sorted(items, key=score_video, reverse=True):
        if v["event"] not in best:
            best[v["event"]] = v

    ranked = sorted(best.values(), key=score_video, reverse=True)

    def brand(v):
        e = norm(v["event"])
        for b in (
            "apple","google","samsung","nvidia","microsoft","snapdragon",
            "meta","aws","adobe","netflix","disney","sxsw","coachella",
        ):
            if b in e:
                return b
        return norm(v["channel"])

    selected = []
    used_brands = set()

    for v in ranked:
        b = brand(v)
        if b in used_brands and len(ranked) > n:
            continue
        selected.append(v)
        used_brands.add(b)
        if len(selected) == n:
            break

    if len(selected) < n:
        selected_ids = {v["id"] for v in selected}
        for v in ranked:
            if v["id"] not in selected_ids:
                selected.append(v)
                if len(selected) == n:
                    break

    return selected[:n]

def trusted_news_source(source):
    s = norm(source)
    return any(hint in s for hint in TRUSTED_NEWS_SOURCE_HINTS)

def google_news_rss(query, lang):
    if lang == "ko":
        params = {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    else:
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}

    r = requests.get(
        "https://news.google.com/rss/search",
        params=params,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    r.raise_for_status()

    root = ET.fromstring(r.content)
    out = []

    for item in root.findall(".//item")[:20]:
        headline = html.unescape(item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()

        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""

        desc = item.findtext("description") or ""
        snippet = re.sub(r"<[^>]+>", " ", desc)
        snippet = re.sub(r"\s+", " ", html.unescape(snippet)).strip()

        if source and not trusted_news_source(source):
            continue

        out.append({
            "headline": headline[:200],
            "source": source or "Google News",
            "snippet": snippet[:400],
            "url": url,
        })

        if len(out) >= 3:
            break

    return out

def collect_articles(event):
    q = f'"{event["event"]}" {event["channel"]}'
    merged = []

    for lang in ("en", "ko"):
        try:
            merged.extend(google_news_rss(q, lang))
        except Exception as e:
            print("News RSS unavailable:", lang, repr(e))

    unique = []
    seen = set()

    for article in merged:
        key = (norm(article["headline"]), norm(article["source"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
        if len(unique) >= 3:
            break

    return unique

def collect_reaction(video_id):
    try:
        data = yt_get("commentThreads", {
            "part": "snippet",
            "videoId": video_id,
            "maxResults": 20,
            "order": "relevance",
            "textFormat": "plainText",
        })
    except Exception:
        return "댓글이 비활성화됐거나 뚜렷한 반응 신호를 확인하기 어려움"

    texts = []
    for item in data.get("items", []):
        text = (
            item.get("snippet", {})
                .get("topLevelComment", {})
                .get("snippet", {})
                .get("textDisplay", "")
        )
        if text:
            texts.append(text)

    if not texts:
        return "뚜렷한 반응 신호 없음"

    counter = collections.Counter()

    for text in texts:
        tokens = re.findall(
            r"[A-Za-z][A-Za-z0-9+-]{2,}|[가-힣]{2,}",
            text.lower()
        )
        for token in tokens:
            if token not in STOPWORDS and not token.startswith("http"):
                counter[token] += 1

    common = [w for w, c in counter.most_common(6) if c >= 2]
    if not common:
        return "댓글 반응이 분산돼 뚜렷한 공통 키워드 없음"

    return "댓글에서 " + ", ".join(common[:4]) + " 언급이 반복됨"

def extractive_what_happened(event, articles):
    desc = re.sub(r"\s+", " ", event.get("description", "")).strip()
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+", desc)
        if len(s.strip()) > 25
    ]

    parts = [sentences[0][:140] if sentences else event["title"][:140]]

    if articles:
        a = articles[0]
        if norm(a["headline"]) not in norm(parts[0]):
            parts.append(f"{a['source']}: {a['headline'][:100]}")

    return " / ".join(parts)[:260]

def generate_event_insights(events):
    # ONLY LLM step.
    if not OPENAI_API_KEY or not OPENAI_MODEL:
        return ["LLM Insight 미설정 — 기사·반응 데이터만 표시"] * len(events)

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    data = []
    for i, e in enumerate(events):
        data.append({
            "index": i,
            "event": e["event"],
            "video_title": e["title"],
            "official_description": e["description"][:500],
            "what_happened": e["what_happened"],
            "media_headlines": [
                f"{a['source']}: {a['headline']}"
                for a in e["articles"][:3]
            ],
            "reaction": e["reaction"],
        })

    prompt = (
        "아래 근거만 사용해 글로벌 이벤트 기획자 관점의 Event Insight를 작성해. "
        "Galaxy Unpacked에 참고 가능한 발표 구조, narrative, demo, staging, audience experience "
        "또는 프로그램 운영 시사점을 우선하되, 근거에 없는 무대/연출은 만들지 마. "
        "일반적인 칭찬 금지. 이벤트당 한국어 1~2문장, 최대 90자. "
        'JSON 배열만 출력: [{"index":0,"insight":"..."}].\n\n'
        + json.dumps(data, ensure_ascii=False)
    )

    resp = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        max_output_tokens=350,
    )

    text = resp.output_text.strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise RuntimeError("LLM output is not JSON")

    parsed = json.loads(text[start:end+1])
    by_index = {
        int(x["index"]): str(x["insight"])[:140]
        for x in parsed
        if "index" in x and "insight" in x
    }
    return [
        by_index.get(i, "근거 부족으로 별도 Event Insight를 만들지 않음")
        for i in range(len(events))
    ]

def esc(value):
    return html.escape(str(value or ""))

def pages_url():
    url = os.environ.get(
        "REPORT_URL",
        "https://yache89-arch.github.io/global-event-radar-report/"
    ).strip()
    if not url.endswith("/"):
        url += "/"
    return url


def build_html(events):
    cards = []

    for i, e in enumerate(events, 1):
        if e["articles"]:
            source_html = "<ul>" + "".join(
                f'<li><a href="{esc(a["url"])}" target="_blank" rel="noopener">'
                f'{esc(a["source"])} — {esc(a["headline"])}</a></li>'
                for a in e["articles"]
            ) + "</ul>"
        else:
            source_html = "<p class='muted'>관련 신뢰 매체 기사 없음</p>"

        badge = "OFFICIAL" if e["tier"] == 2 else "TRUSTED MEDIA"

        cards.append(f"""
<article class="card">
  <div class="eyebrow">#{i} · {esc(badge)}</div>
  <h2>{esc(e["event"])}</h2>
  <div class="video">
    <img src="{esc(e["thumbnail"])}" alt="">
    <div>
      <h3>{esc(e["title"])}</h3>
      <p class="muted">{esc(e["channel"])} · {e["views"]:,} views</p>
      <a class="yt" href="{esc(e["url"])}" target="_blank" rel="noopener">YouTube 보기 ↗</a>
    </div>
  </div>
  <section><h4>What happened</h4><p>{esc(e["what_happened"])}</p></section>
  <section class="insight"><h4>Event Insight</h4><p>{esc(e["insight"])}</p></section>
  <section><h4>Reaction</h4><p>{esc(e["reaction"])}</p></section>
  <section><h4>Sources</h4>{source_html}</section>
</article>""")

    date = dt.datetime.now(KST).strftime("%Y.%m.%d")
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Global Event Radar</title>
<style>
body{{margin:0;background:#f5f6f8;color:#16181d;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Noto Sans KR",sans-serif}}
main{{max-width:820px;margin:auto;padding:34px 18px 64px}}
h1{{margin:0 0 7px;font-size:34px}} .subtitle,.muted{{color:#727985}}
.card{{background:#fff;border:1px solid #e4e7ec;border-radius:18px;padding:24px;margin:0 0 20px}}
.eyebrow{{color:#2453ff;font-size:13px;font-weight:750}} h2{{font-size:27px;margin:7px 0 18px}}
.video{{display:grid;grid-template-columns:200px 1fr;gap:18px;padding-bottom:20px;border-bottom:1px solid #e4e7ec}}
.video img{{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:10px}} h3{{margin:0 0 6px}}
.yt{{display:inline-block;margin-top:9px;color:#2453ff;font-weight:700;text-decoration:none}}
section{{padding-top:18px}} h4{{font-size:14px;margin:0 0 7px;text-transform:uppercase}}
p{{margin:0;line-height:1.6}} .insight{{background:#f0f4ff;margin-top:18px;padding:18px;border-radius:12px}}
.insight h4{{color:#2453ff}} li{{margin:8px 0;line-height:1.45}} li a{{color:#16181d}}
@media(max-width:620px){{.video{{grid-template-columns:1fr}}.card{{padding:18px}}h1{{font-size:28px}}}}
</style>
</head>
<body><main>
<h1>GLOBAL EVENT RADAR</h1>
<p class="subtitle">{date} · Daily Event Intelligence</p>
{''.join(cards)}
</main></body></html>"""

def write_report(events):
    docs = Path("docs")
    docs.mkdir(exist_ok=True)
    page = build_html(events)
    (docs / "index.html").write_text(page, encoding="utf-8")
    date_slug = dt.datetime.now(KST).strftime("%Y-%m-%d")
    (docs / f"{date_slug}.html").write_text(page, encoding="utf-8")

def kakao_access_token():
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "refresh_token": KAKAO_REFRESH_TOKEN,
    }
    if KAKAO_CLIENT_SECRET:
        data["client_secret"] = KAKAO_CLIENT_SECRET

    r = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=data, timeout=30
    )
    r.raise_for_status()
    return r.json()["access_token"]

def compact_kakao_text(events):
    date = dt.datetime.now(KST).strftime("%m.%d")
    lines = [f"GLOBAL EVENT RADAR | {date}"]

    for i, e in enumerate(events[:3], 1):
        summary = e.get("what_happened", "")
        summary = re.sub(r"\s+", " ", summary).strip()
        # Keep each event compact so all Top 3 fit inside Kakao's 200-char text limit.
        if len(summary) > 34:
            summary = summary[:33].rstrip() + "…"
        name = e.get("event", "Event")
        if len(name) > 22:
            name = name[:21].rstrip() + "…"
        lines.append(f"{i}. {name} — {summary}")

    lines.append("상세 내용·Insight·Reaction은 바로 확인하기")
    text = "\n".join(lines)

    # Hard safety for Kakao Text template.
    return text[:200]

def save_latest_payload(events):
    Path("docs").mkdir(exist_ok=True)
    payload = {
        "generated_at": dt.datetime.now(KST).isoformat(),
        "events": [
            {
                "event": e["event"],
                "what_happened": e["what_happened"],
            }
            for e in events[:3]
        ],
    }
    Path("docs/latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def load_latest_payload():
    path = Path("docs/latest.json")
    if not path.exists():
        raise RuntimeError("docs/latest.json not found. Generate report first.")
    return json.loads(path.read_text(encoding="utf-8"))

def send_kakao_from_saved_report():
    url = pages_url()
    payload = load_latest_payload()
    events = payload.get("events", [])

    if not events:
        raise RuntimeError("No events in docs/latest.json")

    token = kakao_access_token()

    template = {
        "object_type": "text",
        "text": compact_kakao_text(events),
        "link": {
            "web_url": url,
            "mobile_web_url": url,
        },
        "buttons": [
            {
                "title": "바로 확인하기",
                "link": {
                    "web_url": url,
                    "mobile_web_url": url,
                },
            }
        ],
    }

    r = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        },
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=30,
    )
    if not r.ok:
        print("Kakao error:", r.status_code, r.text)
    r.raise_for_status()

def generate_report():
    candidates = fetch_youtube_candidates()
    selected = choose_distinct_events(candidates, 3)

    if not selected:
        print("No reliable event candidates. Nothing generated.")
        return

    events = []
    for video in selected:
        articles = collect_articles(video)
        reaction = collect_reaction(video["id"])
        what_happened = extractive_what_happened(video, articles)

        event = dict(video)
        event["articles"] = articles
        event["reaction"] = reaction
        event["what_happened"] = what_happened
        events.append(event)

    insights = generate_event_insights(events)
    for event, insight in zip(events, insights):
        event["insight"] = insight

    write_report(events)
    save_latest_payload(events)

    print(f"OK: {len(events)} distinct events. HTML report generated.")

def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["generate", "send"],
        default="generate",
    )
    args = parser.parse_args()

    if args.mode == "generate":
        generate_report()
    else:
        send_kakao_from_saved_report()
        print("Kakao brief sent after Pages became available.")

if __name__ == "__main__":
    main()
