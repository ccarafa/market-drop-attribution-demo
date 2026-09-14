"""Publish the separately authorized attribution demo; no AWS or runtime credentials."""
import hashlib,json,re,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];NAME='market-drop-attribution-demo'
FILES=['index.html','assets/style.css','assets/app.js','assets/engine.js','data/replay.json','data/manifest.json','README.md','docs/METHOD.md','AGENTS.md','.gitignore','.gitattributes','.nojekyll','package.json','tests/engine.test.js','tools/prepare_data.py','tools/check_browser.py','tools/publish.py','.github/workflows/checks.yml']
def run(args,check=True):
    r=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if check and r.returncode:raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r
def gh(*args,check=True):return run(['gh',*args],check)
def git(*args,check=True):return run(['git','-c','safe.directory='+str(ROOT),*args],check)
def api(path,method,body):
    temp=ROOT/'.runtime/pages-request.json';temp.write_text(json.dumps(body),encoding='utf-8')
    r=gh('api',path,'--method',method,'--input',str(temp))
    return json.loads(r.stdout) if r.stdout.strip() else {}
for name in FILES:
    assert (ROOT/name).is_file(),name
    text=(ROOT/name).read_text(encoding='utf-8')
    assert not re.search(r'-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}',text),name
data=json.loads((ROOT/'data/replay.json').read_text(encoding='utf-8'))
assert len(data['symbols'])==8 and len(data['dates'])==23
assert sum(len(b) for stock in data['bars'].values() for b in stock.values())==8832
assert json.loads((ROOT/'.runtime/browser.json').read_text())['status']=='passed'
run(['node','--test','tests/engine.test.js'])
owner=gh('api','user','--jq','.login').stdout.strip();repo=owner+'/'+NAME
existing=gh('api','repos/'+repo,check=False)
if existing.returncode:
    assert '404' in existing.stderr or '404' in existing.stdout,existing.stderr
    gh('repo','create',repo,'--public','--description','股票下跌归因演示：八只股票历史价格、虚构原因与模拟概率排序，独立浏览器回放')
else:assert json.loads(existing.stdout)['visibility']=='public'
if not (ROOT/'.git').exists():
    git('init','-b','main')
remote=git('remote','get-url','origin',check=False)
if remote.returncode:git('remote','add','origin','https://github.com/'+repo+'.git')
else:assert remote.stdout.strip()=='https://github.com/'+repo+'.git'
git('config','user.name','Z');git('config','user.email','project@localhost')
assert set(git('ls-files').stdout.splitlines())<=set(FILES),'Unexpected tracked files'
git('add','--',*FILES)
if git('diff','--cached','--quiet',check=False).returncode:git('commit','-m','Publish historical drop attribution simulation')
git('-c','credential.helper=','-c','credential.helper=!gh auth git-credential','push','-u','origin','main')
pages=gh('api','repos/'+repo+'/pages',check=False)
if pages.returncode:
    assert '404' in pages.stderr or '404' in pages.stdout,pages.stderr
    info=api('repos/'+repo+'/pages','POST',{'source':{'branch':'main','path':'/'}})
else:info=json.loads(pages.stdout)
if not info.get('https_enforced'):api('repos/'+repo+'/pages','PUT',{'https_enforced':True})
url='https://'+owner+'.github.io/'+NAME+'/'
gh('repo','edit',repo,'--homepage',url)
result={'repository':repo,'visibility':'public','url':url,'commit':git('rev-parse','HEAD').stdout.strip(),'data_sha256':hashlib.sha256((ROOT/'data/replay.json').read_bytes()).hexdigest()}
(ROOT/'.runtime/deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'status':'pushed; Pages build pending','url':url,'commit':result['commit']}))
