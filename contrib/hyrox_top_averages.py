#!/usr/bin/env python3

from __future__ import annotations
import argparse
import json
import math
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

BASE = "https://www.hyresult.com"
RANKINGS_URL = "https://www.hyresult.com/rankings/alltime/hyrox-men"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

def fetch_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")

def get_top_athletes(n: int = 10, url: str = RANKINGS_URL) -> List[Dict[str, str]]:
    soup = fetch_soup(url)
    tbody = soup.select_one("tbody.tremor-TableBody-root") or soup.find("tbody")
    if not tbody:
        raise RuntimeError("Could not find rankings table body.")
    rows = tbody.select("tr.tremor-TableRow-row") or tbody.find_all("tr")
    athletes: List[Dict[str, str]] = []
    for idx, row in enumerate(rows[:n], start=1):
        a = row.select_one('a[href^="/athlete/"]')
        if not a:
            anchors = row.find_all("a", href=True)
            a = next((x for x in anchors if "/athlete/" in x["href"]), None)
        if not a:
            continue
        name = " ".join(a.get_text(" ", strip=True).split())
        profile_url = urljoin(BASE, a["href"])
        # Ensure timings tab
        timings_url = force_timings_tab(profile_url)
        athletes.append({"rank": idx, "name": name, "profile_url": profile_url, "timings_url": timings_url})
    if not athletes:
        raise RuntimeError("No athlete links parsed from rankings.")
    return athletes

def force_timings_tab(url: str) -> str:
    """Return URL with ?tab=timings preserved/added."""
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    q["tab"] = ["timings"]
    new_query = urlencode({k: v[0] if isinstance(v, list) else v for k, v in q.items()})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def get_text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""

def parse_duration_to_seconds(text: str) -> Optional[float]:
    """
    Parse durations like "1:02:31", "50:38", "3:45", "0:59", "12" into seconds.
    Returns None if parsing fails.
    """
    t = text.strip()
    if not t:
        return None
    # Normalize unicode
    t = t.replace("\u2009", "").replace(" ", "")
    # HH:MM:SS or MM:SS or SS
    parts = t.split(":")
    try:
        if len(parts) == 3:
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m, s = map(int, parts)
            return m * 60 + s
        elif len(parts) == 1 and parts[0].isdigit():
            return float(parts[0])
    except ValueError:
        return None
    return None

def seconds_to_hms(seconds: float) -> str:
    if seconds is None or math.isnan(seconds):
        return ""
    seconds = int(round(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    else:
        return f"{m}:{s:02d}"

def fetch_athlete_timings(timings_url: str) -> Dict[str, object]:
    soup = fetch_soup(timings_url)
    # Try to infer name from <h1> or URL
    name = None
    h1 = soup.select_one("h1") or soup.select_one("header h1") or soup.select_one("main h1")
    if h1:
        name = get_text(h1)
    if not name:
        m = re.search(r"/athlete/([^/?#]+)", timings_url)
        if m:
            name = m.group(1).replace("-", " ").title()
    if not name:
        name = "Unknown"
    tbody = soup.select_one("tbody.tremor-TableBody-root") or soup.find("tbody")
    if not tbody:
        raise RuntimeError(f"Could not find timings table body for {timings_url}")
    rows = tbody.select("tr.tremor-TableRow-row") or tbody.find_all("tr")
    timings: List[Dict[str, str]] = []
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 4:
            continue
        split = get_text(tds[0])
        fastest = get_text(tds[1])
        average = get_text(tds[2])
        slowest = get_text(tds[3])
        timings.append({
            "split": split,
            "fastest": fastest,
            "average": average,
            "slowest": slowest,
            "average_seconds": parse_duration_to_seconds(average),
        })
    return {"name": name, "timings_url": timings_url, "timings": timings}

def aggregate_averages(athletes: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """
    Aggregate per-split 'average' values across athletes.
    Returns a list of {split, count, mean_average, mean_average_seconds}.
    """
    # Collect per split
    by_split: Dict[str, List[float]] = {}
    for athlete in athletes:
        for row in athlete["timings"]:
            split = row["split"]
            sec = row.get("average_seconds")
            if sec is None:
                continue
            by_split.setdefault(split, []).append(sec)

    # Compute mean for each split
    out: List[Dict[str, object]] = []
    for split, secs in sorted(by_split.items()):
        if not secs:
            continue
        mean_sec = sum(secs) / len(secs)
        out.append({
            "split": split,
            "count": len(secs),
            "mean_average_seconds": mean_sec,
            "mean_average": seconds_to_hms(mean_sec),
        })
    return out

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10, help="Number of top athletes to aggregate (default 10)")
    parser.add_argument("--rankings-url", default=RANKINGS_URL, help="Rankings page URL")
    parser.add_argument("--sleep", type=float, default=0.5, help="Seconds to sleep between athlete requests (default 0.5)")
    parser.add_argument("--out", default=None, help="Optional path to save JSON")
    args = parser.parse_args()

    top = int(args.top / 2)
    top_athletes_men = get_top_athletes(n=top, url=args.rankings_url)
    top_athletes_women = get_top_athletes(n=top, url="https://www.hyresult.com/rankings/alltime/hyrox-women")
    top_athletes = top_athletes_men + top_athletes_women

    athletes_data: List[Dict[str, object]] = []
    for i, a in enumerate(top_athletes, start=1):
        try:
            data = fetch_athlete_timings(a["timings_url"])
            athletes_data.append(data)
        except Exception as e:
            # Continue but record the error stub
            athletes_data.append({"name": a["name"], "timings_url": a["timings_url"], "error": str(e), "timings": []})
        if i < len(top_athletes) and args.sleep > 0:
            time.sleep(args.sleep)

    aggregate = aggregate_averages(athletes_data)

    payload = {
        "source": {
            "rankings_url": args.rankings_url,
            "base": BASE,
        },
        "params": {
            "top_n": args.top,
            "sleep": args.sleep,
        },
        "rankings": top_athletes,
        "athletes": athletes_data,
        "aggregate": {
            "splits": aggregate
        }
    }

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote JSON to {args.out}")
    else:
        print(text)

if __name__ == "__main__":
    main()
