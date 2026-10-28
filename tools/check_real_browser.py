"""Exercise the real-data study in Edge through a GitHub Pages subpath."""
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import websocket


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.runtime'
OUT.mkdir(exist_ok=True)
PREFIX = '/market-drop-attribution-demo/'


def asset_hashes():
    files = [ROOT / 'evidence.html', ROOT / 'index.html', ROOT / 'demo.html', ROOT / 'data/study.json',
             *sorted((ROOT / 'assets').glob('*.js')), *sorted((ROOT / 'assets').glob('*.css')),
             *sorted((ROOT / 'data/series').glob('*.json'))]
    return {file.relative_to(ROOT).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in files}


initial_asset_hashes = asset_hashes()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if not self.path.startswith(PREFIX):
            self.send_error(404)
            return
        self.path = '/' + self.path[len(PREFIX):]
        if any(part.startswith('.') for part in urlsplit(self.path).path.split('/') if part):
            self.send_error(404)
            return
        super().do_GET()

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = sys.argv[1] if len(sys.argv) > 1 else f'http://127.0.0.1:{server.server_port}{PREFIX}'
if not url.endswith('.html'):
    url = url.rstrip('/') + '/evidence.html'
site_origin = urlsplit(url).netloc
profile = Path(tempfile.mkdtemp(prefix='attribution-real-browser-')).resolve()
browser = Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
proc = subprocess.Popen([
    str(browser), '--headless=new', '--disable-gpu', '--no-first-run',
    '--disable-background-networking', '--disable-extensions',
    '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    '--user-data-dir=' + str(profile), 'about:blank',
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
ws = None
counter = 0
errors = []
requests = []
paused_requests = []


def cdp(method, params=None):
    global counter
    counter += 1
    request_id = counter
    ws.send(json.dumps({'id': request_id, 'method': method, 'params': params or {}}))
    while True:
        response = json.loads(ws.recv())
        if response.get('method') == 'Runtime.exceptionThrown':
            details = response['params']['exceptionDetails']
            errors.append(details.get('exception', {}).get('description', details.get('text', 'Runtime error')))
        if response.get('method') == 'Network.requestWillBeSent':
            requests.append(response['params']['request']['url'])
        if response.get('method') == 'Fetch.requestPaused':
            paused_requests.append(response['params'])
        if response.get('id') == request_id:
            assert 'error' not in response, response
            return response.get('result', {})


def js(expression, promise=False):
    result = cdp('Runtime.evaluate', {
        'expression': expression, 'returnByValue': True, 'awaitPromise': promise,
    })
    assert 'exceptionDetails' not in result, result
    return result['result'].get('value')


def screenshot(name):
    (OUT / (name + '.png')).write_bytes(base64.b64decode(
        cdp('Page.captureScreenshot', {'format': 'png'})['data']))


def wait(expression, timeout=30):
    deadline = time.time() + timeout
    while not js(expression):
        if time.time() > deadline:
            screenshot('real-failure')
            raise AssertionError((expression, errors, js('document.body.innerText.slice(-1800)')))
        time.sleep(.15)


def click(selector):
    js('document.querySelector(' + json.dumps(selector) + ').click()')


def field(selector, value, event='change'):
    js('(()=>{const el=document.querySelector(' + json.dumps(selector) + ');el.value=' + json.dumps(value)
       + ';el.dispatchEvent(new Event(' + json.dumps(event) + ',{bubbles:true}));})()')


def hook(expression):
    return js('window.attributionStudy.' + expression)


def ready():
    wait("window.attributionStudy?.selectedEvent && window.attributionStudy?.history?.length>0 && window.attributionStudy?.loadState==='ready'")


def check_history():
    ready()
    selected = hook('selectedEvent')
    history = hook('history')
    assert history and all(str(row['date']) <= str(selected['date']) for row in history)
    assert all(row['symbol'] == selected['symbol'] for row in history)
    assert str(history[-1]['date']) == str(selected['date'])
    assert js("document.querySelector('#study-chart')!==null")
    chart_dates = js("JSON.parse(document.querySelector('#study-chart').dataset.dates)")
    assert [str(date) for date in chart_dates] == [str(row['date']) for row in history]
    return selected, history


def exercise():
    # Hold the initial JSON request long enough to inspect the loading state.
    cdp('Fetch.enable', {'patterns': [{'urlPattern': '*/data/*', 'requestStage': 'Request'}]})
    cdp('Page.navigate', {'url': url})
    wait("document.querySelector('#study-status') && /加载|读取|载入/.test(document.querySelector('#study-status').textContent)")
    assert not js('Boolean(window.attributionStudy?.selectedEvent)')
    assert js("document.querySelector('#study-export').disabled")
    deadline = time.time() + 10
    while not paused_requests:
        js('document.readyState')
        assert time.time() < deadline, 'Study JSON was not requested'
        time.sleep(.1)
    cdp('Fetch.disable')
    ready()

    data = hook('data')
    summary = data['summary']
    assert summary['stocks'] == 5210 and summary['dates'] == 23 and summary['stock_days'] == 119652
    assert len(data['dates']) == 23
    assert all(str(date).startswith('202607') for date in data['dates'])
    body = js('document.body.innerText')
    assert '5,210' in body or '5210' in body
    assert '23' in body and '2026' in body
    assert '真实' in body
    assert not js("document.querySelector('.reason-probability,[data-probability]')!==null")
    columns = data['columns']
    events = [dict(zip(columns, row)) for row in data['events']]
    assert len(events) == summary['raw_events']
    assert any(row['screened'] for row in events) and any(not row['screened'] for row in events)
    selected, history = check_history()
    assert selected['return'] < -.05
    source = next(row for row in events if row['symbol'] == selected['symbol'] and row['date'] == selected['date'])
    assert all(selected[key] == value for key, value in source.items())
    assert 'reasons' not in selected and 'probabilities' not in selected
    screenshot('real-desktop')

    # Stock search, no-result recovery, date filtering and paging are real UI actions.
    field('#study-search', 'NO_SUCH_STOCK', 'input')
    wait('window.attributionStudy.filteredEvents.length===0')
    assert js("document.querySelectorAll('#study-event-list [data-event-id]').length") == 0
    assert any(word in js("document.querySelector('#study-event-list').textContent") for word in ['没有', '暂无', '未找到', '无匹配'])
    click('#study-reset')
    wait('window.attributionStudy.filteredEvents.length>0')
    field('#study-search', selected['symbol'], 'input')
    wait('window.attributionStudy.filteredEvents.length>0')
    assert all(row['symbol'] == selected['symbol'] for row in hook('filteredEvents'))
    click('#study-reset')
    date_value = str(data['dates'][-1])
    field('#study-date', date_value)
    wait('window.attributionStudy.filteredEvents.length>0')
    assert all(str(row['date']) == date_value for row in hook('filteredEvents'))
    click('#study-reset')
    first_page_ids = js("[...document.querySelectorAll('#study-event-list [data-event-id]')].map(el=>el.dataset.eventId)")
    assert first_page_ids and not js("document.querySelector('#study-next').disabled")
    click('#study-next')
    next_page_ids = js("[...document.querySelectorAll('#study-event-list [data-event-id]')].map(el=>el.dataset.eventId)")
    assert next_page_ids and first_page_ids != next_page_ids
    click('#study-prev')
    assert first_page_ids == js("[...document.querySelectorAll('#study-event-list [data-event-id]')].map(el=>el.dataset.eventId)")

    field('#study-sort', 'decline')
    returns = [row['return'] for row in hook('filteredEvents')]
    assert returns == sorted(returns)
    field('#study-sort', 'volume')
    volumes = [row['volume_ratio'] for row in hook('filteredEvents') if row['volume_ratio'] is not None]
    assert volumes == sorted(volumes, reverse=True)
    field('#study-sort', 'date_desc')
    dates = [row['date'] for row in hook('filteredEvents')]
    assert dates == sorted(dates, reverse=True)

    for quality in ['clean', 'flagged']:
        field('#study-quality', quality)
        wait('window.attributionStudy.filteredEvents.length>0')
        subset = hook('filteredEvents')
        assert all(row['screened'] for row in subset) if quality == 'clean' else all(not row['screened'] for row in subset)
        click('#study-event-list [data-event-id]')
        current, _ = check_history()
        assert current['screened'] is (quality == 'clean')
        if quality == 'flagged':
            assert current['flags']
            assert js("document.querySelector('#study-quality-flags').textContent.trim().length>0")
        assert js("document.querySelector('#study-evidence').textContent.trim().length>0")
    js('document.querySelector("#study-selection").scrollIntoView({block:"start",behavior:"instant"})')
    screenshot('real-detail')

    # Verify the actual downloaded Blob, without opening a browser download directory.
    js("window._originalBlobURL=URL.createObjectURL;URL.createObjectURL=blob=>{window._exportPayload=blob.text();return window._originalBlobURL(blob);};window._anchorClick=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){if(!this.download)return window._anchorClick.call(this);};")
    expected_export = hook('getExport()')
    click('#study-export')
    payload = json.loads(js('window._exportPayload', True))
    assert payload.pop('exported_at', None)
    assert payload == expected_export
    assert payload['event'] == hook('selectedEvent')
    assert payload.get('provenance')
    assert payload['kind'] == 'historical_daily_event_evidence' and payload['simulated'] is False
    assert payload['history'] == hook('history')
    assert all(str(row['date']) <= str(payload['event']['date']) for row in hook('history'))

    # Cached stock history should still work when every subsequent request is blocked.
    cdp('Network.setBlockedURLs', {'urls': ['*']})
    current = hook('selectedEvent')
    hook('selectEvent(' + json.dumps(current['symbol'] + '_' + str(current['date'])) + ')')
    check_history()
    cdp('Network.setBlockedURLs', {'urls': []})

    # The first observed day must not invent a five-observation history baseline.
    hook('selectEvent("000070_20260701")')
    early, early_history = check_history()
    assert early['date'] == 20260701 and len(early_history) == 1
    for key in ['volume_ratio', 'spread_ratio', 'imbalance_change']:
        assert early[key] is None
    assert any(word in js("document.querySelector('#study-evidence').textContent") for word in ['不足', '缺失', '暂无', '—'])

    cdp('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
    click('#study-reset')
    ready()
    wait("document.querySelector('#study-toast').hidden")
    assert js('document.documentElement.scrollWidth<=window.innerWidth+1')
    js('document.querySelector("#study-selection").scrollIntoView({block:"start",behavior:"instant"})')
    assert js('document.documentElement.scrollWidth<=window.innerWidth+1')
    assert js("(()=>{const a=document.querySelector('#study-selection').getBoundingClientRect(),b=document.querySelector('#study-chart').getBoundingClientRect(),c=document.querySelector('#study-evidence').getBoundingClientRect();return a.top>=0&&a.top<40&&a.bottom<=b.top&&b.bottom<=c.top&&[a,b,c].every(r=>r.left>=0&&r.right<=innerWidth);})()")
    screenshot('real-mobile-detail')
    js('document.querySelector("#study-evidence").scrollIntoView({block:"start",behavior:"instant"})')
    assert js("(()=>{const r=[...document.querySelectorAll('#study-evidence .evidence-card')].map(e=>e.getBoundingClientRect());return r.length===3&&r.every((x,i)=>x.left>=0&&x.right<=innerWidth&&(!i||r[i-1].bottom<=x.top+1));})()")
    screenshot('real-mobile-evidence')
    js('window.scrollTo({top:0,left:0,behavior:"instant"})')
    screenshot('real-mobile')

    # Network failures must leave a useful, truthful error state.
    cdp('Network.setCacheDisabled', {'cacheDisabled': True})
    cdp('Network.setBlockedURLs', {'urls': ['*/data/*']})
    cdp('Page.reload', {'ignoreCache': True})
    wait("document.querySelector('#study-status') && /无法|失败|错误|不可用/.test(document.querySelector('#study-status').textContent)")
    assert not js('Boolean(window.attributionStudy?.selectedEvent)')
    assert js("document.querySelector('#study-export').disabled")
    cdp('Network.setBlockedURLs', {'urls': []})
    cdp('Page.reload', {'ignoreCache': True})
    ready()

    demo = js("[...document.querySelectorAll('a[href]')].find(el=>el.getAttribute('href').split('?')[0]==='demo.html')?.href")
    assert demo and urlsplit(demo).netloc == site_origin
    cdp('Page.navigate', {'url': demo})
    wait('window.attributionDemo?.engine?.data?.symbols?.length===8')
    assert '真实证据回放' in js("document.querySelector('.truth-banner p').textContent")
    assert js("document.querySelector('#return-study')!==null")
    click('#return-study')
    # The demo returns to the site homepage, which is now the attribution workbench.
    wait("window.attributionCauses?.selected && document.querySelector('#attr-status').hidden")
    home = js('location.pathname')
    assert home.endswith('/index.html') or home.endswith('/'), home
    cdp('Page.navigate', {'url': url})
    ready()
    return {
        'stocks': summary['stocks'], 'dates': summary['dates'],
        'stock_days': summary['stock_days'], 'raw_events': summary['raw_events'],
        'actual_july_2026_coverage': 'passed', 'real_event_matches_source': 'passed',
        'filters_sort_pagination': 'passed', 'missing_case_recovery': 'passed',
        'screened_and_flagged_cases': 'passed', 'as_of_stock_history': 'passed',
        'missing_prior_history_stays_missing': 'passed',
        'export_matches_selection_and_provenance': 'passed', 'loading_and_fetch_failure': 'passed',
        'cached_history_without_network': 'passed', 'preserved_demo_return_home': 'passed',
        'mobile_width': 390, 'mobile_chart_evidence_layout': 'passed',
    }


try:
    deadline = time.time() + 20
    while not (profile / 'DevToolsActivePort').exists():
        assert time.time() < deadline, 'Browser startup failed'
        time.sleep(.2)
    port = (profile / 'DevToolsActivePort').read_text().splitlines()[0]
    targets = json.load(urllib.request.urlopen('http://127.0.0.1:' + port + '/json'))
    target = next(target for target in targets if target['type'] == 'page')
    ws = websocket.create_connection(target['webSocketDebuggerUrl'], suppress_origin=True, timeout=30)
    cdp('Runtime.enable')
    cdp('Network.enable')
    cdp('Page.enable')
    cdp('Emulation.setDeviceMetricsOverride', {'width': 1480, 'height': 1060, 'deviceScaleFactor': 1, 'mobile': False})
    checks = exercise()
    assert not errors, errors
    network = [request for request in requests if request.startswith(('http:', 'https:'))]
    assert all(urlsplit(request).netloc == site_origin for request in network), network
    assert not any('/api/' in urlsplit(request).path for request in network), network
    assert asset_hashes() == initial_asset_hashes, 'Site assets changed during browser checks; rerun checks.'
    report = {
        'status': 'passed', 'deployment': 'public_https' if url.startswith('https:') else 'local_subpath',
        **checks, 'api_network_requests': 0, 'external_network_requests': 0, 'javascript_errors': errors,
        'asset_sha256': initial_asset_hashes,
    }
    suffix = 'real-browser-live' if url.startswith('https:') else 'real-browser'
    (OUT / (suffix + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
finally:
    if ws:
        try:
            cdp('Browser.close')
        except Exception:
            pass
        ws.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.terminate()
        proc.wait(timeout=5)
    server.shutdown()
    server.server_close()
    assert profile.parent == Path(tempfile.gettempdir()).resolve()
    assert profile.name.startswith('attribution-real-browser-')
    shutil.rmtree(profile, ignore_errors=True)
