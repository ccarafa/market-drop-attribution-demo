const $=id=>document.getElementById(id);
const escapeText=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const dateText=value=>value?String(value).replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3'):'—';
const stamp=value=>value?String(value).replace('T',' ').slice(0,16):'—';
const number=value=>Number.isFinite(value)?value.toLocaleString('zh-CN'):'—';
const STATUS={connected:'已接入',verified_not_used:'已验证，未用于站点数据',attention:'需要关注'};
const CHECK={connected_sample:'通过',connected:'通过',no_data:'无数据',entitlement_required:'无权限',authentication_failed:'凭证无效',configuration_required:'未配置',transport_failed:'网络失败',unexpected_response:'响应异常',attention:'需要关注'};
const pill=(status,labels)=>`<span class="status-pill ${escapeText(status)}">${escapeText(labels[status]||status)}</span>`;
function status(message,error=false){$('sources-status').textContent=message;$('sources-status').hidden=!message;$('sources-status').dataset.error=String(error);}
async function fetchJSON(path){const response=await fetch(new URL(`../${path}`,import.meta.url));if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json();}
function coverage(c){if(!c)return '';const parts=[];if(c.days)parts.push(`<span>${number(c.days)}<small>天</small></span>`);if(c.first_day)parts.push(`<span>${dateText(c.first_day)} — ${dateText(c.last_day)}</span>`);if(c.rows)parts.push(`<span>${number(c.rows)}<small>条</small></span>`);if(c.fetched_at)parts.push(`<span>${stamp(c.fetched_at)}<small>拉取（UTC）</small></span>`);if(c.complete===false)parts.push('<span class="status-pill attention">未完整</span>');return `<div class="coverage-line">${parts.join('')}</div>`;}
function renderSources(data){
  $('sources-list').innerHTML=data.sources.map(s=>`<article class="source-card"><h3>${escapeText(s.name)}<code>${escapeText(s.api)}</code>${pill(s.status,STATUS)}</h3><dl><dt>路线</dt><dd>${escapeText(s.route)}</dd><dt>鉴权</dt><dd>${escapeText(s.auth)}</dd><dt>用途</dt><dd>${escapeText(s.use)}</dd><dt>字段</dt><dd>${escapeText(s.fields)}</dd><dt>计费</dt><dd>${escapeText(s.billing)}</dd><dt>限额</dt><dd>${escapeText(s.limit)}</dd></dl>${coverage(s.coverage)}</article>`).join('');
  $('credentials-list').innerHTML=data.credentials.map(c=>`<article class="credential-card"><h3>${escapeText(c.label)}<code>${escapeText(c.name)}</code></h3><dl><dt>获取</dt><dd>${escapeText(c.obtained)}</dd><dt>用于</dt><dd>${c.used_by.map(escapeText).join('、')}</dd><dt>存放</dt><dd><ul>${c.stored_in.map(x=>`<li>${escapeText(x)}</li>`).join('')}</ul></dd></dl></article>`).join('');
  $('rotation-steps').innerHTML=data.rotation.map(x=>`<li>${escapeText(x)}</li>`).join('');
  $('link-secrets').href=data.links.secrets;$('link-workflow').href=data.links.workflow;$('link-runs').href=data.links.runs;
  const check=data.last_check;
  $('check-list').innerHTML=check?`<div class="check-row"><div><strong>总体</strong><small>检查时间 ${stamp(check.checked_at)} UTC · 范围 ${escapeText(check.request_scope?.trade_date)} / ${escapeText(check.request_scope?.ticker)} · 最多 ${number(check.request_scope?.max_requests)} 次请求</small></div><div>Secret 配置：${Object.entries(check.secrets_present||{}).map(([k,v])=>`${escapeText(k)} ${v?'已配置':'未配置'}`).join('；')}</div><div></div><div>${pill(check.overall,CHECK)}</div></div>`+check.probes.map(p=>`<div class="check-row"><div><strong>${escapeText(p.name)}</strong><small>${escapeText(p.route)}</small></div><div>${escapeText(p.auth)}</div><div>HTTP ${escapeText(p.http_status??'—')} · ${number(p.request_count)} 次${p.rows!==undefined?` · ${number(p.rows)} 行`:''}</div><div>${pill(p.status,CHECK)}</div></div>`).join(''):'<p class="check-empty">尚无构建时记录的连接检查。</p>';
  $('available-list').innerHTML=data.available.map(a=>`<li><strong>${escapeText(a.name)}</strong>：${escapeText(a.reason)}</li>`).join('');
  $('sources-generated').textContent=stamp(data.generated_at);
  $('sources-build').textContent=`公告 ${number(data.build?.evidence_counts?.announcements)} 条 · 文章 ${number(data.build?.evidence_counts?.news)} 篇 · 资金流向 ${number(data.build?.moneyflow_rows)} 行`;
}
async function initialize(){
  status('正在载入数据接入清单…');
  try{const data=await fetchJSON('data/sources.json');if(data.schema_version!==1||!Array.isArray(data.sources))throw Error('清单格式无效');renderSources(data);status('');window.attributionSources={data};}
  catch(error){status(`数据接入清单加载失败（${error.message}）。`,true);}
}
initialize();
