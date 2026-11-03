#!/usr/bin/env python3
"""TorresHike $100 camping monitor. Setup and usage: README.md."""
import argparse
import datetime as dt
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode, urljoin, urlparse, parse_qs
import requests
from bs4 import BeautifulSoup

CAMPS = {'Chileno': 2, 'Frances': 4, 'Cuernos': 5}
START, END = dt.date(2027, 3, 12), dt.date(2027, 3, 27)
BASE = 'https://torreshike.com'
TARGET = Decimal('100.00')

def search_url(camp):
    return BASE + '/en/search?' + urlencode(dict(tab_id='custom', accommodation_types='camping',
        **{'from': str(START), 'to': str(END)}, persons=2, currency='USD',
        itinerary=json.dumps([[CAMPS[camp]]], separators=(',', ':')), sort='by_price'))

def parse_results(body, camp):
    soup = BeautifulSoup(body, 'html.parser')
    cards = soup.select('[data-testid="search-result-card"]')
    if not cards:
        # Fail closed: zero cards could be a blocked/error/incomplete response.
        raise ValueError(f'{camp}: no result cards; cannot verify this response')
    results = []
    for card in cards:
        nights = card.select('[data-testid="search-accom-night"]')
        price = card.select_one('a[data-testid="search-result-price"]')
        if len(nights) != 1 or price is None:
            raise ValueError(f'{camp}: unexpected card structure')
        link = urljoin(BASE, price['href'])
        q = parse_qs(urlparse(link).query)
        if q.get('persons') != ['2'] or q.get('currency') != ['USD'] or q.get('itinerary') != [str(CAMPS[camp])]:
            raise ValueError(f'{camp}: unexpected people, currency or camp')
        day = dt.date.fromisoformat(q['startDate'][0])
        camp_text = nights[0].get_text(' ', strip=True)
        normalized = camp_text.replace('é', 'e').lower()
        if camp.lower() not in normalized or not nights[0].select_one('[data-testid="search-accom-camping"]'):
            raise ValueError(f'{camp}: cannot confirm camping or camp name')
        text = price.get_text(' ', strip=True)
        m = re.fullmatch(r'(?:from\s+)?([\d,]+\.\d{2})\s+USD', text)
        if not m:
            raise ValueError(f'{camp}: unrecognized price {text!r}')
        amount = Decimal(m[1].replace(',', ''))
        if START <= day <= END:
            results.append(dict(camp=camp, date=str(day), price=str(amount), url=link,
                                key=f'{camp}:{day}'))
    if not results:
        raise ValueError(f'{camp}: no valid results in date range')
    return results

def notify(message):
    token, chat = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    if token or chat:
        if not token or not chat:
            raise ValueError('Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID')
        response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat, 'text': message, 'disable_web_page_preview': True}, timeout=30)
        # Never print the exception URL: it contains the secret bot token.
        if response.status_code != 200 or not response.json().get('ok'):
            raise RuntimeError('Telegram delivery failed')
    elif sys.platform == 'darwin':
        script = 'on run argv\ndisplay notification (item 1 of argv) with title "TorresHike — $100" sound name "Glass"\nend run'
        subprocess.run(['osascript', '-e', script, message], check=True)
    else:
        raise RuntimeError('Configure Telegram to deliver notifications on this system')

def check(state_path, dry_run=False):
    state = json.loads(state_path.read_text()) if state_path.exists() else {'camps': {}}
    failed = False
    session = requests.Session()
    session.headers.update({'User-Agent': 'TorresHikeAvailabilityMonitor/1.0', 'Cache-Control': 'no-cache'})
    for camp in CAMPS:
        try:
            response = session.get(search_url(camp), timeout=60)
            response.raise_for_status()
            rows = parse_results(response.text, camp)
            matches = {r['key']: r for r in rows if Decimal(r['price']) == TARGET}
            previous = state['camps'].get(camp, {}).get('matches', {})
            fresh = [r for k, r in matches.items() if k not in previous]
            print(f'{dt.datetime.now().isoformat(timespec="seconds")} {camp}: {len(rows)} results; {len(matches)} at $100; {len(fresh)} new', flush=True)
            for r in fresh:
                message = f"TorresHike: {camp}, {r['date']}: camping for TWO, from US$100 total for one night.\n{r['url']}"
                print(message, flush=True)
                if not dry_run:
                    notify(message)
            if not dry_run:
                state['camps'][camp] = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'matches': matches}
                state_path.parent.mkdir(parents=True, exist_ok=True)
                temp = state_path.with_suffix('.tmp')
                temp.write_text(json.dumps(state, indent=2))
                temp.replace(state_path)
        except Exception as exc:
            # Keep this camp's previous state on failures; don't report it as sold out.
            failed = True
            print(f'{camp}: check/delivery failed ({type(exc).__name__}). Previous state retained.', file=sys.stderr, flush=True)
        time.sleep(2)
    return not failed

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--once', action='store_true', help='Check once and exit')
    p.add_argument('--dry-run', action='store_true', help='Print matches without notification or state changes')
    p.add_argument('--interval', type=int, default=900, help='Seconds between rounds (default 900, minimum 300)')
    p.add_argument('--state', type=Path, default=Path(__file__).with_name('state.json'))
    a = p.parse_args()
    if a.interval < 300:
        p.error('Use an interval of at least 300 seconds')
    while True:
        if dt.datetime.now(dt.timezone.utc).date() > END:
            print('Monitoring window has ended.'); return
        ok = check(a.state, a.dry_run)
        if a.once:
            sys.exit(0 if ok else 1)
        time.sleep(a.interval)

if __name__ == '__main__':
    main()
