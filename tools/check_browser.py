"""Check the static attribution demo in Edge under a project Pages subpath."""
import base64,hashlib,json,os,shutil,subprocess,sys,tempfile,threading,time,urllib.request
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import websocket

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.runtime';OUT.mkdir(exist_ok=True)
PREFIX='/market-drop-attribution-demo/'
def asset_hashes():
    files=[ROOT/'demo.html',ROOT/'data/replay.json',*sorted((ROOT/'assets').glob('*.js')),*sorted((ROOT/'assets').glob('*.css'))]
    return {file.relative_to(ROOT).as_posix():hashlib.sha256(file.read_bytes()).hexdigest() for file in files if file.name in {'demo.html','replay.json','app.js','engine.js','style.css'}}
initial_asset_hashes=asset_hashes()
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kw):super().__init__(*args,directory=str(ROOT),**kw)
    def do_GET(self):
        if not self.path.startswith(PREFIX):self.send_error(404);return
        self.path='/'+self.path[len(PREFIX):]
        if any(part.startswith('.') for part in urlsplit(self.path).path.split('/') if part):self.send_error(404);return
        super().do_GET()
    def log_message(self,*args):pass

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
url=sys.argv[1] if len(sys.argv)>1 else f'http://127.0.0.1:{server.server_port}'+PREFIX
parts=urlsplit(url)
if parts.path.endswith('/index.html'):
    url=parts._replace(path=parts.path[:-10]+'demo.html').geturl()
elif not parts.path.endswith('.html'):
    url=parts._replace(path=parts.path.rstrip('/')+'/demo.html').geturl()
site_origin=urlsplit(url).netloc
profile=Path(tempfile.mkdtemp(prefix='attribution-browser-')).resolve()
browser=Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
proc=subprocess.Popen([str(browser),'--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--disable-extensions','--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
ws=None;counter=0;errors=[];requests=[]
def cdp(method,params=None):
    global counter
    counter+=1;rid=counter;ws.send(json.dumps({'id':rid,'method':method,'params':params or {}}))
    while True:
        r=json.loads(ws.recv())
        if r.get('method')=='Runtime.exceptionThrown':errors.append(r['params']['exceptionDetails'].get('exception',{}).get('description',r['params']['exceptionDetails'].get('text','Runtime error')))
        if r.get('method')=='Network.requestWillBeSent':requests.append(r['params']['request']['url'])
        if r.get('id')==rid:
            assert 'error' not in r,r
            return r.get('result',{})
def js(expr,promise=False):
    r=cdp('Runtime.evaluate',{'expression':expr,'returnByValue':True,'awaitPromise':promise})
    assert 'exceptionDetails' not in r,r
    return r['result'].get('value')
def wait(expr,timeout=20):
    end=time.time()+timeout
    while not js(expr):
        if time.time()>end:
            screenshot('failure');raise AssertionError((expr,errors,js('document.body.innerText.slice(-1500)')))
        time.sleep(.15)
def screenshot(name):(OUT/(name+'.png')).write_bytes(base64.b64decode(cdp('Page.captureScreenshot',{'format':'png'})['data']))
def click(selector):js('document.querySelector('+json.dumps(selector)+').click()')
def state():return js('window.attributionDemo.engine.state')

def exercise():
    wait('window.attributionDemo?.engine?.data?.symbols?.length===8')
    assert len(js('window.attributionDemo.engine.data.dates'))==23
    assert state()['cursor']==48900
    assert js("document.querySelectorAll('#date-select option').length")==23
    assert js("document.querySelectorAll('#stock-tape [data-symbol]').length")==8
    assert js('document.title')=='市场事件归因系统'
    assert js("document.querySelector('.truth-banner p').textContent.trim()")=='Demo'
    assert js("document.querySelector('#popup-disclaimer').textContent.trim()")=='Demo'
    screenshot('initial')
    click('#step');wait("document.getElementById('event-dialog').open")
    first=state()['currentEvents'][0]
    assert first['symbol']=='000011' and first['seconds']==49200
    assert abs(first['change']-(-.066006600660066))<1e-10
    assert js("document.querySelectorAll('#popup-reasons .reason-item').length")==3
    probs=js("[...document.querySelectorAll('#popup-reasons .reason-probability strong')].map(x=>parseFloat(x.textContent))")
    assert probs==sorted(probs,reverse=True) and sum(probs)==100
    assert all(p%10!=0 for p in probs)
    assert all(x=="Demo" for x in js("[...document.querySelectorAll('#popup-reasons .reason-description')].map(x=>x.textContent.trim())"))
    assert not any(x in js("document.getElementById('event-dialog').textContent") for x in ["模拟情景","模拟情形","预设情景","模拟概率"])
    assert all('Demo' in x for x in js("[...document.querySelectorAll('#popup-reasons .reason-item')].map(x=>x.textContent)"))
    assert all('概率' in x for x in js("[...document.querySelectorAll('#popup-reasons .reason-item')].map(x=>x.textContent)"))
    assert js("document.getElementById('popup-disclaimer').textContent.trim()")=='Demo'
    screenshot('popup')
    click('#review-event');wait("!document.getElementById('event-dialog').open")
    assert js('window.attributionDemo.selectedSymbol')=='000011'
    assert js('window.attributionDemo.engine.visibleBars("000011").every(b=>(b.session===0?34200:46800)+(b.bar+1)*300<=window.attributionDemo.engine.state.cursor)')
    screenshot('workspace')
    # Search affects chart selection, never monitoring universe.
    js("document.getElementById('symbol-search').value='600000';document.getElementById('symbol-search').dispatchEvent(new Event('input'))")
    click('[data-symbol="600000"]');assert js('window.attributionDemo.selectedSymbol')=='600000'
    js("document.getElementById('symbol-search').value='';document.getElementById('symbol-search').dispatchEvent(new Event('input'))")
    js("document.getElementById('seek').value=48900;document.getElementById('seek').dispatchEvent(new Event('input'))")
    assert len(state()['currentEvents'])==0
    assert not js("document.getElementById('event-dialog').open")
    click('#step');wait("document.getElementById('event-dialog').open")
    assert state()['currentEvents'][0]['symbol']=='000011'
    click('#close-dialog')
    # A same-origin, multi-stock crossing must expose each separate three-reason popup.
    simultaneous=js("(()=>{const e=window.attributionDemo.engine;for(const d of e.data.dates){const a=e.eventCatalog(d);for(const x of a){const group=a.filter(y=>y.seconds===x.seconds);if(group.length>1)return {date:d,seconds:x.seconds,symbols:group.map(y=>y.symbol)};}}})()")
    assert simultaneous
    js("document.getElementById('date-select').value="+json.dumps(str(simultaneous['date']))+";document.getElementById('date-select').dispatchEvent(new Event('change'))")
    js("document.getElementById('seek').value="+str(simultaneous['seconds']-300)+";document.getElementById('seek').dispatchEvent(new Event('input'))")
    click('#step');wait("document.getElementById('event-dialog').open")
    assert len(state()['currentEvents'])==len(simultaneous['symbols'])
    shown=[]
    for _ in simultaneous['symbols']:
        assert js("document.querySelectorAll('#popup-reasons .reason-item').length")==3
        shown.append(js("document.getElementById('popup-title').textContent.split(' ')[0]"));click('#review-event')
    assert sorted(shown)==sorted(simultaneous['symbols'])
    assert not js("document.getElementById('event-dialog').open")
    # Complete static data remains usable after every subsequent network request is blocked.
    cdp('Network.setBlockedURLs',{'urls':['*']})
    click('#next-event');wait("document.getElementById('event-dialog').open")
    click('#review-event')
    while js("document.getElementById('event-dialog').open"):click('#review-event')
    click('.nav-button[data-page=history]');assert not js("document.getElementById('page-history').hidden")
    assert js("document.querySelectorAll('#history-list [data-event-id]').length")==len(state()['events'])
    # Verify export payload without writing into a browser's default download directory.
    js("window._originalBlobURL=URL.createObjectURL;URL.createObjectURL=b=>{window._exportPayload=b.text();return window._originalBlobURL(b);};window._anchorClick=HTMLAnchorElement.prototype.click;HTMLAnchorElement.prototype.click=function(){if(!this.download)return window._anchorClick.call(this);};")
    click('#export');payload=json.loads(js('window._exportPayload',True))
    assert payload['all_reasons_and_probabilities_are_fictional'] is True
    assert payload['events'] and all(e['simulated'] and len(e['reasons'])==3 for e in payload['events'])
    screenshot('history')
    # Opening an older event and returning to its workspace must show that event's date and basis.
    js("document.querySelector('#history-list [data-event-id=\"1\"]').click()")
    click('#review-event')
    assert state()['date']==first['date'] and state()['cursor']==first['seconds'] and state()['basis']==first['basis']
    click('.nav-button[data-page=method]')
    assert '收盘价' in js("document.getElementById('page-method').textContent")
    assert js("document.querySelector('#page-method [data-page=monitor]')!==null")
    assert js("document.querySelector('#page-history [data-page=monitor]')!==null")
    screenshot('method')
    cdp('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
    for page in ['monitor','history','method']:
        click('.nav-button[data-page='+page+']');time.sleep(.2)
        assert js('document.documentElement.scrollWidth<=window.innerWidth+1'),page
    click('.nav-button[data-page=monitor]');screenshot('mobile')
    click('#next-event');wait("document.getElementById('event-dialog').open")
    assert js("document.getElementById('event-dialog').getBoundingClientRect().width<=window.innerWidth")
    assert js("document.querySelectorAll('#popup-reasons .reason-item').length")==3
    screenshot('mobile-popup')
    return {'pages':3,'stocks':8,'dates':23,'real_historical_threshold_trigger':'passed','three_ranked_fictional_reasons':'passed','all_stock_monitoring':'passed','simultaneous_event_queue':'passed','silent_seek':'passed','historical_event_chart_alignment':'passed','no_future_candles':'passed','network_blocked_replay':'passed','export_markers':'passed','mobile_width':390}

try:
    end=time.time()+20
    while not (profile/'DevToolsActivePort').exists():
        assert time.time()<end,'Browser startup failed';time.sleep(.2)
    port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
    targets=json.load(urllib.request.urlopen('http://127.0.0.1:'+port+'/json'))
    ws=websocket.create_connection(next(t for t in targets if t['type']=='page')['webSocketDebuggerUrl'],suppress_origin=True,timeout=30)
    cdp('Runtime.enable');cdp('Network.enable');cdp('Page.enable')
    cdp('Emulation.setDeviceMetricsOverride',{'width':1480,'height':1060,'deviceScaleFactor':1,'mobile':False})
    cdp('Page.navigate',{'url':url})
    checks=exercise()
    assert not errors,errors
    network=[x for x in requests if x.startswith(('http:','https:'))]
    assert all(urlsplit(x).netloc==site_origin for x in network),network
    assert not any('/api/' in urlsplit(x).path for x in network),network
    assert asset_hashes()==initial_asset_hashes,'Site assets changed during browser checks; rerun checks.'
    report={'status':'passed','deployment':'public_https' if url.startswith('https:') else 'local_subpath',**checks,'api_network_requests':0,'javascript_errors':errors,'asset_sha256':initial_asset_hashes}
    suffix='browser-live' if url.startswith('https:') else 'browser'
    (OUT/(suffix+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report))
finally:
    if ws:
        try:cdp('Browser.close')
        except Exception:pass
        ws.close()
    try:proc.wait(timeout=5)
    except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=5)
    server.shutdown();server.server_close()
    assert profile.parent==Path(tempfile.gettempdir()).resolve() and profile.name.startswith('attribution-browser-')
    shutil.rmtree(profile,ignore_errors=True)
