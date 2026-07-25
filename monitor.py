"""
DAAD New Programs Daily Monitor - Playwright Edition
- Uses real Chromium browser to bypass DAAD anti-bot
"""

import os
import json
import sys
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

WX_PUSHER_APP_TOKEN = os.environ.get('WXPUSHER_APP_TOKEN', '').strip()
WX_PUSHER_UID = os.environ.get('WXPUSHER_UID', '').strip()
SNAPSHOT_PATH = 'snapshot.json'
LOG_PATH = 'monitor_run.log'
BEIJING_TZ = timezone(timedelta(hours=8))

DAAD_URLS = [
    'https://www.daad.de/en/study-in-germany/universities/all-degree-programmes/?q=&degree=2&lang=2',
    'https://www.daad.de/de/in-deutschland-studieren/hochschulen/alle-studiengaenge/?q=&degree=2&lang=2',
]


def log(msg):
    ts = datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def fetch_daad():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage']
        )
        try:
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                locale='en-US',
                timezone_id='Europe/Berlin',
                viewport={'width': 1920, 'height': 1080},
                extra_http_headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9,de;q=0.8',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'Upgrade-Insecure-Requests': '1',
                }
            )
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page = context.new_page()

            for url in DAAD_URLS:
                try:
                    log(f'Browser fetching: {url[:80]}...')
                    page.goto(url, wait_until='networkidle', timeout=40000)
                    try:
                        page.wait_for_selector('article.c-result, .program-item, .s-result-list__item, .c-result', timeout=15000)
                    except Exception:
                        log('Selector wait timeout')
                    page.wait_for_timeout(3000)
                    html = page.content()
                    log(f'Got HTML length={len(html)}')
                    if len(html) > 5000:
                        return html
                except Exception as e:
                    log(f'URL error: {type(e).__name__}: {str(e)[:120]}')
            raise Exception('All URLs failed')
        finally:
            browser.close()


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
                programs.append({'uni_de': 'Unknown', 'program_de': name, 'url': href})
    if not programs:
        log('Broad fallback...')
        for a in soup.select('a[href]'):
            href = a.get('href', '').strip()
            if any(p in href for p in ['/study/', '/studium/', '/programme', '/program']):
                if href.startswith('/'):
                    href = 'https://www.daad.de' + href
                name = a.get_text(strip=True)
                if len(name) > 8:
                    programs.append({'uni_de': 'Unknown', 'program_de': name, 'url': href})
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
        log(f'Snapshot parse failed: {e}')
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
        log('ERROR: missing credentials')
        sys.exit(1)
    log('===== DAAD Daily Monitor Start =====')
    try:
        html = fetch_daad()
    except Exception as e:
        log(f'Fetch failed: {e}')
        sys.exit(1)
    programs = parse_programs(html)
    log(f'Parsed {len(programs)} programs')
    if not programs:
        log('No programs found')
        sys.exit(1)
    old = load_snapshot()
    old_urls = {p['url'] for p in old if 'url' in p}
    new_programs = [p for p in programs if p['url'] not in old_urls]
    log(f'Previous: {len(old)}; New: {len(new_programs)}')
    if new_programs:
        lines = []
        for p in new_programs[:30]:
            lines.append(f"* {p['uni_de']} | {p['program_de']}\n  Link: {p['url']}")
        body = "\n\n".join(lines)
        msg = (
            "[DAAD Monitor] New Master English programs detected\n"
            f"Time: {beijing}\n"
            f"New count: {len(new_programs)}\n\n"
            f"{body}\n\n"
            "Tip: minimal version, links go to DAAD."
        )
        try:
            result = push_wxpusher(msg, 'DAAD new programs')
            log(f'Push result: {result}')
        except Exception as e:
            log(f'Push failed: {e}')
    else:
        log('No new, silent')
    save_snapshot(programs)
    log('===== Done =====')


if __name__ == '__main__':
    main()
