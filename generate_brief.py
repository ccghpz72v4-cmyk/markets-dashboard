#!/usr/bin/env python3
"""
Daily market dashboard generator.

Sources:
- BBC RSS feeds
- Google News RSS queries restricted to Reuters/AP/FT
- Nasdaq's public earnings-calendar endpoint for upcoming earnings

AI:
- Gemini API, using GEMINI_API_KEY from GitHub Actions secrets.

Outputs:
- data/brief.json
- data/world-news.json
- data/company-events.json

Never put an API key in the public HTML.
"""

import datetime as dt
import time
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

WATCHLIST = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "GOOGL": "Alphabet",
    "AMZN": "Amazon",
    "META": "Meta",
    "JPM": "JPMorgan",
    "V": "Visa",
    "BRK.B": "Berkshire Hathaway",
    "ASML": "ASML",
    "TSLA": "Tesla",
    "AZN": "AstraZeneca",
    "SHEL": "Shell",
    "HSBC": "HSBC",
    "AVGO": "Broadcom",
}

HEADERS = {
    "User-Agent": "MarketsDashboard/1.0 (personal educational dashboard)"
}

def fetch(url, timeout=25, extra_headers=None):
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()

def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value

def parse_rss(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []

    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link"))
        description = clean_text(item.findtext("description"))
        pub = clean_text(item.findtext("pubDate"))

        if title and link:
            items.append({
                "title": title,
                "url": link,
                "description": description[:600],
                "published": pub
            })

    return items

def fetch_bbc():
    feeds = [
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://feeds.bbci.co.uk/news/technology/rss.xml",
    ]
    results = []

    for feed in feeds:
        try:
            results.extend(parse_rss(fetch(feed)))
        except Exception as exc:
            print(f"BBC feed failed: {exc}")

    return results

def fetch_google_news(query):
    encoded = urllib.parse.quote(query)
    url = (
        "https://news.google.com/rss/search?"
        f"q={encoded}&hl=en-GB&gl=GB&ceid=GB:en"
    )

    try:
        return parse_rss(fetch(url))
    except Exception as exc:
        print(f"Google News feed failed: {exc}")
        return []

def dedupe(items):
    seen = set()
    output = []

    for item in items:
        key = re.sub(r"[^a-z0-9]+", " ", item["title"].lower()).strip()
        if key and key not in seen:
            seen.add(key)
            output.append(item)

    return output

def get_headlines():
    items = fetch_bbc()

    queries = [
        "(site:reuters.com OR site:apnews.com) world business markets",
        "(site:reuters.com OR site:apnews.com) economy finance markets",
        "(site:ft.com OR site:reuters.com) global markets business",
    ]

    for query in queries:
        items.extend(fetch_google_news(query))

    return dedupe(items)[:60]

def call_gemini(headlines):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    compact = []
    for i, item in enumerate(headlines[:50], start=1):
        compact.append(
            f"{i}. {item['title']} | {item['description']} | {item['url']}"
        )

    prompt = f"""
You are the editor of a beginner-friendly morning financial and business briefing.

Today is {today}.

Use ONLY the supplied headlines/descriptions as factual source material.
Do not invent facts, numbers, events, causes, quotes, or market moves.

Your job is to:
1. identify the five most important WORLD/business/economic/technology stories;
2. write a short market brief explaining the day's main developments.

Return valid JSON only, with this exact structure:
{{
  "selected_headline_numbers": [1, 2, 3, 4, 5],
  "brief": "..."
}}

The selected headline numbers must refer to the supplied list.

For the brief, use exactly these sections:

WHAT HAPPENED
- 3 to 5 short bullets.

WHY IT MATTERS
- 2 to 4 short bullets.
- Explain finance jargon in plain English.

WHAT TO WATCH TODAY
- 3 to 5 bullets describing scheduled or clearly developing things to watch.
- Do not predict prices or market outcomes.

IN SIMPLE TERMS
- One short paragraph, maximum 80 words.

Rules:
- If the material does not establish why something happened, say that the reason is unclear rather than guessing.
- Distinguish reported facts from interpretation.
- Do not give investment advice.
- Do not predict winners, losers, prices, or election outcomes.
- Keep the whole brief under 450 words.

HEADLINES:
{chr(10).join(compact)}
"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json"
        }
    }

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(GEMINI_MODEL, safe='')}:generateContent"
        f"?key={urllib.parse.quote(GEMINI_API_KEY)}"
    )

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "MarketsDashboard/1.0"
        },
        method="POST",
    )

    max_attempts = 4
    delays = [5, 15, 30]

    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
            break

        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504):
                raise

            if attempt == max_attempts - 1:
                raise

            print(
                f"Gemini temporarily unavailable (HTTP {error.code}). "
                f"Retrying in {delays[attempt]} seconds..."
            )
            time.sleep(delays[attempt])

        except urllib.error.URLError:
            if attempt == max_attempts - 1:
                raise

            print(
                f"Gemini connection problem. "
                f"Retrying in {delays[attempt]} seconds..."
            )
            time.sleep(delays[attempt])

    try:
        raw = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        return json.loads(raw)
    except Exception:
        raise RuntimeError(
            "Gemini returned an unexpected response: "
            + json.dumps(result)[:2500]
        )
def fetch_nasdaq_earnings():
    """
    Pull the next 7 calendar days from Nasdaq's public earnings calendar.
    This is a public web endpoint used by Nasdaq's earnings page; it is not
    treated as a guaranteed API contract, so failures simply leave the
    company-events section empty rather than breaking the whole dashboard.
    """
    events = []
    today = dt.date.today()

    for offset in range(0, 8):
        day = today + dt.timedelta(days=offset)
        url = (
            "https://api.nasdaq.com/api/calendar/earnings?"
            + urllib.parse.urlencode({"date": day.isoformat()})
        )

        try:
            raw = fetch(
                url,
                extra_headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://www.nasdaq.com",
                    "Referer": "https://www.nasdaq.com/",
                },
            )
            data = json.loads(raw.decode("utf-8"))
            rows = ((data.get("data") or {}).get("rows") or [])

            for row in rows:
                symbol = clean_text(str(row.get("symbol") or "")).upper()
                normalized = symbol.replace("/", ".")
                if normalized in WATCHLIST:
                    session = clean_text(
                        str(
                            row.get("time") or
                            row.get("reportTime") or
                            row.get("reportDate") or
                            "Time not supplied"
                        )
                    )
                    events.append({
                        "date": day.isoformat(),
                        "company": WATCHLIST[normalized],
                        "symbol": normalized,
                        "session": session
                    })
        except Exception as exc:
            print(f"Nasdaq earnings lookup failed for {day}: {exc}")

    # Deduplicate and sort.
    unique = {}
    for event in events:
        key = (event["date"], event["symbol"])
        unique[key] = event

    return sorted(unique.values(), key=lambda x: (x["date"], x["company"]))

def main():
    headlines = get_headlines()
    if not headlines:
        raise RuntimeError("No news headlines were retrieved.")

    ai = call_gemini(headlines)

    selected_numbers = ai.get("selected_headline_numbers", [])
    selected = []

    for number in selected_numbers:
        try:
            index = int(number) - 1
            if 0 <= index < len(headlines):
                selected.append(headlines[index])
        except (TypeError, ValueError):
            pass

    # Safe fallback if the model's selection is malformed.
    if len(selected) < 5:
        selected = headlines[:5]

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    (DATA / "brief.json").write_text(
        json.dumps(
            {
                "updated_at": now,
                "brief": ai.get("brief", "").strip()
            },
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    (DATA / "world-news.json").write_text(
        json.dumps(
            {
                "updated_at": now,
                "headlines": selected[:5]
            },
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    events = fetch_nasdaq_earnings()
    (DATA / "company-events.json").write_text(
        json.dumps(
            {
                "updated_at": now,
                "events": events
            },
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print(
        f"Generated brief, {len(selected[:5])} world headlines, "
        f"and {len(events)} company events."
    )

if __name__ == "__main__":
    main()
