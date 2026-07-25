"""
DAAD New Programs Daily Monitor
- Fetches DAAD Master English program list (with retry + fallback)
- Compares with previous snapshot
- Pushes only NEW programs to WxPusher (personal WeChat)
- Silent when no new programs
"""

import os
import json
import sys
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

# ----- Config -----
WX_PUSHER_APP_TOKEN = os.environ.get('WXPUSHER_APP_TOKEN', '').strip()
WX_PUSHER_UID = os.environ.get('WXPUSHER_UID', '').strip()
SNAPSHOT_PATH = 'snapshot.json'
LOG_PATH = 'monitor_run.log'
BEIJING_TZ = timezone(timedelta(hours=8))

# DAAD URLs to try in order (primary + fallbacks)
DAAD_URLS = [
    'https://www.daad.de/en/study-in-germany/universities/all-degree-programmes/?q=&degree=2&lang=2',
    'https://www.daad.de/de/in-deutschland-studieren/hochschulen/alle-studiengaenge/?q=&degree=2&lang=2',
    'https://www.daad.de/en/study-in-germany/universities/all-degree-programmes/',
]


def log(msg):
    ts = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def make_session():
    """Create a requests session with retry logic and realistic browser headers."""
    session = requests.Session()
    retry_strategy = Retry(
        total=4,
        backoff_factor=2,
        status_forcelist=[403, 429, 500, 502, 503, 504],
        allowed_methods=['GET', 'HEAD'],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'en-US,en;q=0.9,de;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
        'Connection': 'keep-alive',
        'DNT': '1',
    })
    return session


def warm_up_session(session, url):
    try:
        home = 'https://www.daad.de/en/'
        session.get(home, timeout=10, allow_redirects=True)
        time.sleep(1)
    except Exception as e:
        log(f'Warmup warning: {e}')


def fetch_daad(session):
    warm_up_session(session, DAAD_URLS[0])
    for i, url in enumerate(DAAD_URLS):
        try:
            log(f'Trying URL #{i+1}: {url[:80]}...')
            headers = {'Referer': 'https://www.daad.de/en/'}
            r = session.get(url, headers=headers, timeout=25, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 1000:
                log(f'Success with URL #{i+1}, length={len(r.text)}')
                return r.text
            else:
                log(f'URL #{i+1} returned status={r.status_code}, length={len(r.text)}')
        except requests.exceptions.RetryError as e:
            log(f'URL #{i+1} exhausted retries: {str(e)[:100]}')
        except Exception as e:
            log(f'URL #{i+1} error: {type(e).__name__}: {str(e)[:100]}')
        if i < len(DAAD_URLS) - 1:
            time.sleep(3)
    raise Exception('All DAAD URLs failed (likely anti-bot blocking)')


def parse_programs(html):
    soup = BeautifulSoup(html, 'html.parser')
    programs = []
    for art in soup.select('article.c-result, div.s-result-list__item, .program-item, li.c-result'):
        name_el = art.select_one('.c-result__name, h4, .program-title, h3')
        uni_el = art.select_one('.c-result__title, h3, .uni-title, .c-result__subtitle')
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
    if not programs:
        for a in soup.select('a[href*="/en/study/"], a[href*="/studium/"], a[href*="/en/programs/"], a[href*="/en/degree-programmes/"]'):
            href = a.get('href', '').strip()
            if not href:
                continue
            if href.startswith('/'):
                href = 'https://www.daad.de' + href
            name = a.get_text(strip=True)
            if len(name) > 5 and 'view all' not in name.lower():
                programs.append({
                    'uni_de': 'Unknown',
                    'program_de': name,
                    'url': href
                })
    if not programs:
        log('Falling back to broad URL extraction...')
        for a in soup.select('a[href]'):
            href = a.get('href', '').strip()
            if any(p in href for p in ['/study/', '/studium/', '/programme', '/program']):
                if href.startswith('/'):
                    href = 'https://www.daad.de' + href
                name = a.get_text(strip=True)
                if len(name) > 8:
                    programs.append({
                        'uni_de': 'Unknown',
                        'program_de': name,
                        'url': href
                    })
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
    try:
        session = make_session()
        html = fetch_daad(session)
    except Exception as e:
        log(f'Fetch failed completely: {e}')
        sys.exit(1)
    programs = parse_programs(html)
    log(f'Parsed {len(programs)} programs')
    if not programs:
        log('No programs found. DAAD page structure may have changed.')
        with open('last_html.html', 'w', encoding='utf-8') as f:
            f.write(html[:50000])
        log('Saved first 50KB of HTML to last_html.html for debugging')
        sys.exit(1)
    old = load_snapshot()
    old_urls = {p['url'] for p in old if 'url' in p}
    new_programs = [p for p in programs if p['url'] not in old_urls]
    log(f'Previous snapshot: {len(old)} programs; New this round: {len(new_programs)}')
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
    save_snapshot(programs)
    log('Snapshot saved')
    log('===== Run Finished =====')


if __name__ == '__main__':
    main()
