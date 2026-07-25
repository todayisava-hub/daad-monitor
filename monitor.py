"""
DAAD New Programs Daily Monitor
- Fetches DAAD Master English program list
- Compares with previous snapshot
- Pushes only NEW programs to WxPusher (personal WeChat)
- Silent when no new programs
"""

import os
import json
import sys
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

# ----- Config -----
WX_PUSHER_APP_TOKEN = os.environ.get('WXPUSHER_APP_TOKEN', '').strip()
WX_PUSHER_UID = os.environ.get('WXPUSHER_UID', '').strip()
SNAPSHOT_PATH = 'snapshot.json'
LOG_PATH = 'monitor_run.log'
DAAD_URL = 'https://www.daad.de/de/in-deutschland-studieren/hochschulen/alle-studiengaenge/?q=&degree=2&lang=2'
BEIJING_TZ = timezone(timedelta(hours=8))


def log(msg):
    ts = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def fetch_daad():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    r = requests.get(DAAD_URL, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text


def parse_programs(html):
    """Extract program list from DAAD search result page (multi-strategy fallback)."""
    soup = BeautifulSoup(html, 'html.parser')
    programs = []

    # Strategy 1: article.c-result blocks
    for art in soup.select('article.c-result, div.s-result-list__item, .program-item'):
        name_el = art.select_one('.c-result__name, h4, .program-title')
        uni_el = art.select_one('.c-result__title, h3, .uni-title')
        a_el = art.select_one('a[href]')
        if not (name_el and a_el):
            continue
        href = a_el.get('href', '').strip()
        if not href:
            continue
        if href.startswith('/'):
            href = 'https://www.daad.de' + href
        programs.append({
            'uni_de': uni_el.get_text(strip=True) if uni_el else 'Unknown',
            'program_de': name_el.get_text(strip=True),
            'url': href
        })

    # Strategy 2: fallback - any link to study-related paths
    if not programs:
        for a in soup.select('a[href*="/en/study/"], a[href*="/studium/"], a[href*="/en/programs/"]'):
            href = a.get('href', '').strip()
            if not href:
                continue
            if href.startswith('/'):
                href = 'https://www.daad.de' + href
            name = a.get_text(strip=True)
            if len(name) > 3:
                programs.append({
                    'uni_de': 'Unknown',
                    'program_de': name,
                    'url': href
                })

    # Dedup by URL (keep first occurrence)
    seen = set()
    deduped = []
    for p in programs:
        if p['url'] not in seen:
            seen.add(p['url'])
            deduped.append(p)
    return deduped


def load_snapshot():
    if not os.path.exists(SNAPSHOT_PATH):
        return []
    try:
        with open(SNAPSHOT_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        log(f'Snapshot parse failed: {e} (treating as first run)')
        return []


def save_snapshot(programs):
    with open(SNAPSHOT_PATH, 'w', encoding='utf-8') as f:
        json.dump(programs, f, ensure_ascii=False, indent=2)


def push_wxpusher(content, summary):
    url = 'https://wxpusher.zjiecode.com/api/send/message'
    data = {
        'appToken': WX_PUSHER_APP_TOKEN,
        'content': content,
        'summary': summary,
        'contentType': 1,
        'uids': [WX_PUSHER_UID]
    }
    r = requests.post(url, json=data, timeout=10)
    r.raise_for_status()
    return r.json()


def main():
    beijing = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M')

    if not (WX_PUSHER_APP_TOKEN and WX_PUSHER_UID):
        log('ERROR: WXPUSHER_APP_TOKEN or WXPUSHER_UID not set in environment')
        sys.exit(1)

    log('===== DAAD Daily Monitor Start =====')

    # 1. Fetch
    try:
        html = fetch_daad()
        log(f'Fetched DAAD page, length={len(html)}')
    except Exception as e:
        log(f'Fetch failed: {e}')
        sys.exit(1)

    # 2. Parse
    programs = parse_programs(html)
    log(f'Parsed {len(programs)} programs')
    if not programs:
        log('No programs found. DAAD page structure may have changed.')
        sys.exit(1)

    # 3. Load snapshot & diff
    old = load_snapshot()
    old_urls = {p['url'] for p in old if 'url' in p}
    new_programs = [p for p in programs if p['url'] not in old_urls]
    log(f'Previous snapshot: {len(old)} programs; New this round: {len(new_programs)}')

    # 4. Push if new
    if new_programs:
        lines = []
        for p in new_programs[:30]:
            lines.append(f"* {p['uni_de']} | {p['program_de']}\n  Link: {p['url']}")
        body_lines = "\n\n".join(lines)

        msg = (
            "[DAAD Monitor] New Master English programs detected\n"
            f"Time: {beijing}\n"
            f"New count: {len(new_programs)}\n\n"
            f"{body_lines}\n\n"
            "Tip: minimal version, links point to DAAD original pages."
        )

        try:
            result = push_wxpusher(msg, 'DAAD new programs')
            log(f'Push result: {result}')
        except Exception as e:
            log(f'Push failed: {e}')
            sys.exit(1)
    else:
        log('No new programs, silent (no push)')

    # 5. Save snapshot for next comparison
    save_snapshot(programs)
    log('Snapshot saved')
    log('===== Run Finished =====')


if __name__ == '__main__':
    main()