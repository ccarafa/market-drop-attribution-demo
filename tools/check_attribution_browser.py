"""Exercise the attribution workbench in Edge through a GitHub Pages subpath."""
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
PAGE = 'index.html'
TOLERANCE = 1e-12


def asset_hashes():
    files = [ROOT / PAGE, ROOT / 'sources.html', ROOT / 'data/sources.json', ROOT / 'data/attribution/index.json',
             *sorted((ROOT / 'assets').glob('*.js')), *sorted((ROOT / 'assets').glob('*.css')),
             *sorted((ROOT / 'data/attribution/events').glob('*.json'))]
    return {file.relative_to(ROOT).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest() for file in files}


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
base = sys.argv[1] if len(sys.argv) > 1 else f'http://127.0.0.1:{server.server_port}{PREFIX}'
url = base + PAGE
site_origin = urlsplit(url).netloc
profile = Path(tempfile.mkdtemp(prefix='attribution-causes-browser-')).resolve()
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
        if response.get('id') == request_id:
            assert 'error' not in response, response
            return response.get('result', {})


def js(expression, promise=False):
    result = cdp('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': promise})
    assert 'exceptionDetails' not in result, result
    return result['result'].get('value')


def screenshot(name):
    (OUT / (name + '.png')).write_bytes(base64.b64decode(cdp('Page.captureScreenshot', {'format': 'png'})['data']))


def wait(expression, timeout=40):
    deadline = time.time() + timeout
    while not js(expression):
        if time.time() > deadline:
            screenshot('causes-failure')
            raise AssertionError((expression, errors, js('document.body.innerText.slice(-1800)')))
        time.sleep(.15)


def click(selector):
    js('document.querySelector(' + json.dumps(selector) + ').click()')


def field(selector, value, event='change'):
    js('(()=>{const el=document.querySelector(' + json.dumps(selector) + ');el.value=' + json.dumps(value)
       + ';el.dispatchEvent(new Event(' + json.dumps(event) + ',{bubbles:true}));})()')


def hook(expression):
    return js('window.attributionCauses.' + expression)


def ready():
    wait("window.attributionCauses?.selected && document.querySelector('#attr-status').hidden")


def check_selected(direction=None, threshold=None):
    ready()
    event = hook('selected')
    shares = event['shares']
    causes = event['causes']
    assert abs(sum(shares) - 1) < 1e-6, shares
    assert abs(sum(c['p'] for c in causes) + shares[0] + shares[1] - 1) < 1e-4, (causes, shares)
    assert all(0 <= c['p'] <= 1 for c in causes)
    assert causes[-1]['p'] <= causes[0]['p'] and any(c['f'] == 'flow' for c in causes)
    if direction:
        assert (event['r'] < 0) if direction == 'down' else (event['r'] > 0)
    if threshold:
        assert abs(event['r']) > float(threshold) + TOLERANCE
    assert js("document.querySelectorAll('#attr-causes .cause-item').length") == len(causes)
    assert js("document.querySelector('#attr-share-bar').children.length") == 3
    assert js("document.querySelector('#attr-evidence').textContent.trim().length>0")
    if js("document.querySelectorAll('#attr-evidence .evidence-toggle').length")>0:
        assert js("[...document.querySelectorAll('#attr-evidence .item-list')].every(ul=>[...ul.querySelectorAll('li:not([hidden])')].length<=3)")
        click('#attr-evidence .evidence-toggle')
        assert js("document.querySelector('#attr-evidence .evidence-toggle').previousElementSibling.querySelectorAll('li[data-extra][hidden]').length")==0
        click('#attr-evidence .evidence-toggle')
        assert js("document.querySelector('#attr-evidence .evidence-toggle').previousElementSibling.querySelectorAll('li[data-extra]:not([hidden])').length")==0
    return event


def exercise():
    cdp('Page.navigate', {'url': url})
    wait("document.querySelector('#attr-status') && /载入/.test(document.querySelector('#attr-status').textContent)")
    assert js("document.querySelector('#attr-export').disabled")
    ready()
    index = hook('index')
    summary = index['summary']
    assert summary['stock_days'] == 119652 and len(summary['dates']) == 23
    assert summary['strict_down5_events'] == 13246
    assert index['metadata']['calculation_type'] == 'retrospective_in_sample_association'
    body = js('document.body.innerText')
    assert '样本内' in body and '不是因果' in body
    first = check_selected('down', '0.05')
    assert first['t'] == 0.05
    date_value = js("document.querySelector('#attr-date').value")
    assert str(first['date']) == date_value
    labels = index['feature_labels']
    for cause in first['causes']:
        if cause['f'] != 'flow':
            assert cause['f'] in labels, cause['f']
    screenshot('causes-desktop')

    # Direction and threshold filters, industry filter, search, sort and paging.
    field('#attr-direction', 'up')
    wait("window.attributionCauses.filtered.length>0")
    up_event = check_selected('up', '0.05')
    assert up_event['r'] > 0
    count_5 = len(hook('events'))
    field('#attr-threshold', '0.02')
    wait("window.attributionCauses.events.length>" + str(count_5))
    wait("window.attributionCauses.filtered.length>0")
    count_2 = len(hook('events'))
    assert all(abs(row['r']) > 0.02 + TOLERANCE for row in hook('filtered'))
    assert any(0.02 < abs(row['r']) <= 0.03 for row in hook('events'))
    field('#attr-direction', 'down')
    field('#attr-threshold', '0.03')
    wait("window.attributionCauses.events.length<" + str(count_2) + "&&window.attributionCauses.events.length>" + str(count_5) + "&&window.attributionCauses.filtered.length>0")
    assert all(row['r'] < -0.03 - TOLERANCE for row in hook('filtered'))
    assert not any(abs(row['r']) <= 0.03 for row in hook('events'))
    sample = hook('selected')
    for ref in sample['ev']['news_company'] + sample['ev']['news_industry']:
        assert str(ref['id']) in hook('shard.news'), ref
    industries = js("[...document.querySelectorAll('#attr-industry option')].map(o=>o.value).filter(Boolean)")
    assert industries
    field('#attr-industry', industries[0])
    wait("window.attributionCauses.filtered.length>0")
    assert all(row['industry'] == industries[0] for row in hook('filtered'))
    field('#attr-industry', '')
    field('#attr-search', 'NO_SUCH_STOCK', 'input')
    wait('window.attributionCauses.filtered.length===0')
    assert any(word in js("document.querySelector('#attr-event-list').textContent") for word in ['没有', '未找到'])
    assert js("document.querySelector('#attr-export').disabled")
    click('#attr-reset')
    wait('window.attributionCauses.filtered.length>0')
    target = check_selected('down', '0.05')
    field('#attr-search', target['symbol'], 'input')
    wait('window.attributionCauses.filtered.length>0')
    assert all(row['symbol'] == target['symbol'] for row in hook('filtered'))
    click('#attr-reset')
    wait('window.attributionCauses.filtered.length>0')
    first_page = js("[...document.querySelectorAll('#attr-event-list [data-event-id]')].map(el=>el.dataset.eventId)")
    if not js("document.querySelector('#attr-next').disabled"):
        click('#attr-next')
        second_page = js("[...document.querySelectorAll('#attr-event-list [data-event-id]')].map(el=>el.dataset.eventId)")
        assert second_page and second_page != first_page
        click('#attr-prev')
    field('#attr-sort', 'specific')
    specific = [row['shares'][2] for row in hook('filtered')]
    assert specific == sorted(specific, reverse=True)
    field('#attr-sort', 'top_cause')
    tops = [max(c['p'] for c in row['causes']) for row in hook('filtered')]
    assert tops == sorted(tops, reverse=True)
    field('#attr-sort', 'magnitude')
    magnitudes = [abs(row['r']) for row in hook('filtered')]
    assert magnitudes == sorted(magnitudes, reverse=True)

    # Another date loads a different shard; its events belong to that date.
    other_date = str(summary['dates'][0])
    field('#attr-date', other_date)
    wait("window.attributionCauses.shard && String(window.attributionCauses.shard.date)===" + json.dumps(other_date))
    ready()
    assert all(str(row['date']) == other_date for row in hook('events'))
    check_selected('down', '0.05')
    js('document.querySelector("#attr-selection").scrollIntoView({block:"start",behavior:"instant"})')
    screenshot('causes-detail')

    # Export payload equals the selected event and carries the calibration table.
    js("window._originalBlobURL=URL.createObjectURL;URL.createObjectURL=blob=>{window._exportPayload=blob.text();return window._originalBlobURL(blob);};window._anchorClick=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){if(!this.download)return window._anchorClick.call(this);};")
    expected = hook('exportPayload()')
    click('#attr-export')
    payload = json.loads(js('window._exportPayload', True))
    assert payload['event'] == expected['event'] and payload['calibration'] == expected['calibration']
    assert payload['kind'] == 'daily_event_attribution' and payload['in_sample_association'] is True and payload['simulated'] is False
    assert payload['metadata']['limitations']

    cdp('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
    click('#attr-reset')
    ready()
    assert js('document.documentElement.scrollWidth<=window.innerWidth+1')
    js('document.querySelector("#attr-selection").scrollIntoView({block:"start",behavior:"instant"})')
    assert js('document.documentElement.scrollWidth<=window.innerWidth+1')
    assert js("(()=>{const a=document.querySelector('#attr-selection').getBoundingClientRect(),b=document.querySelector('#attr-causes').getBoundingClientRect(),c=document.querySelector('#attr-evidence').getBoundingClientRect();return a.top>=0&&a.top<40&&a.bottom<=b.top&&b.bottom<=c.top&&[a,b,c].every(r=>r.left>=0&&r.right<=innerWidth);})()")
    screenshot('causes-mobile-detail')
    js('window.scrollTo({top:0,left:0,behavior:"instant"})')
    screenshot('causes-mobile')

    # Network failure leaves a truthful error state; recovery restores the page.
    cdp('Network.setCacheDisabled', {'cacheDisabled': True})
    cdp('Network.setBlockedURLs', {'urls': ['*/data/*']})
    cdp('Page.reload', {'ignoreCache': True})
    wait("document.querySelector('#attr-status') && /失败|错误|不可用/.test(document.querySelector('#attr-status').textContent)")
    assert not js('Boolean(window.attributionCauses?.selected)')
    assert js("document.querySelector('#attr-export').disabled")
    cdp('Network.setBlockedURLs', {'urls': []})
    cdp('Page.reload', {'ignoreCache': True})
    ready()

    evidence = js("[...document.querySelectorAll('a[href]')].find(el=>el.getAttribute('href').split('?')[0]==='evidence.html')?.href")
    assert evidence and urlsplit(evidence).netloc == site_origin
    demo = js("[...document.querySelectorAll('a[href]')].find(el=>el.getAttribute('href').split('?')[0]==='demo.html')?.href")
    assert demo and urlsplit(demo).netloc == site_origin

    # The data-sources page lists every route, links token rotation to GitHub and never shows a secret.
    sources = js("[...document.querySelectorAll('a[href]')].find(el=>el.getAttribute('href').split('?')[0]==='sources.html')?.href")
    assert sources and urlsplit(sources).netloc == site_origin
    cdp('Page.navigate', {'url': sources})
    wait("window.attributionSources?.data && document.querySelector('#sources-status').hidden")
    manifest = js('window.attributionSources.data')
    assert len(manifest['sources']) >= 5 and js("document.querySelectorAll('#sources-list .source-card').length") == len(manifest['sources'])
    assert js("document.querySelector('#link-secrets').href").startswith('https://github.com/') and 'settings/secrets/actions' in js("document.querySelector('#link-secrets').href")
    assert 'actions/workflows/' in js("document.querySelector('#link-workflow').href")
    page_text = js('document.body.innerText')
    assert '数据接入' in page_text and 'DATAYES_API_TOKEN' in page_text
    import re as _re
    assert not _re.search(r'\b[0-9a-f]{32,}\b', page_text), 'secret-like string on sources page'
    assert js('document.documentElement.scrollWidth<=window.innerWidth+1')
    screenshot('sources-mobile')
    cdp('Emulation.setDeviceMetricsOverride', {'width': 1480, 'height': 1060, 'deviceScaleFactor': 1, 'mobile': False})
    cdp('Page.reload', {'ignoreCache': True})
    wait("window.attributionSources?.data && document.querySelector('#sources-status').hidden")
    assert js('document.documentElement.scrollWidth<=window.innerWidth+1')
    screenshot('sources-desktop')
    return {
        'stock_days': summary['stock_days'], 'events_total': summary['events_total'],
        'strict_down5_events': summary['strict_down5_events'],
        'probability_mass_conserved': 'passed', 'filters_sort_pagination': 'passed',
        'date_shards': 'passed', 'export_matches_selection': 'passed',
        'loading_and_fetch_failure': 'passed', 'mobile_width': 390, 'mobile_layout': 'passed',
        'sources_page_without_secrets': 'passed',
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
    suffix = 'causes-browser-live' if url.startswith('https:') else 'causes-browser'
    (OUT / (suffix + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ['status', 'deployment', 'events_total', 'strict_down5_events']}))
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
    assert profile.name.startswith('attribution-causes-browser-')
    shutil.rmtree(profile, ignore_errors=True)
