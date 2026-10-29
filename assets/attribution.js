import {PAGE_SIZE,isFiniteNumber as finite} from './study-core.js?v=20261015-1';

const $=id=>document.getElementById(id);
const escapeText=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const dateText=value=>String(value??'').replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3');
const number=(value,digits=2)=>finite(value)?value.toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';
const percent=(value,digits=2)=>finite(value)?`${value>0?'+':''}${number(value*100,digits)}%`:'—';
const share=value=>finite(value)?`${number(value*100,0)}%`:'—';
const magnitude=value=>finite(value)?`${number(Math.abs(value)*100,2)}%`:'—';
const whole=value=>finite(value)?Math.round(value).toLocaleString('zh-CN'):'—';
const ratio=value=>finite(value)?`${number(value,2)}×`:'—';
const compact=value=>!finite(value)?'—':Math.abs(value)>=1e8?`${number(value/1e8,2)} 亿`:Math.abs(value)>=1e4?`${number(value/1e4,2)} 万`:number(value,0);
const timeText=value=>value?String(value).slice(5,16).replace('T',' '):'—';
const dateTime=value=>value?String(value).replace('T',' ').replace('+08:00','（上海时间）'):'未提供';
const barTime=label=>{if(typeof label!=='string')return'—';const [session,bar]=label.split(':').map(Number);if(!Number.isInteger(session)||!Number.isInteger(bar))return'—';const minutes=(session===0?9*60+30:13*60)+bar*5;return `${String(Math.floor(minutes/60)).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}`;};
const fallbackFlags={invalid_return:'当日收益无效',invalid_price:'价格记录无效',reference_history_missing:'缺少上一观测日，参考价连续性未核查',reference_gap_gt_1pct:'参考前收与上一观测日末价相差超过 1%',extreme_return_gt_50pct:'当日收益绝对值超过 50%',volume_history_lt_5:'成交量此前 5 日有效观察不足',spread_history_lt_5:'价差此前 5 日有效观察不足',imbalance_history_lt_5:'盘口失衡此前 5 日有效观察不足',invalid_volume:'当日成交量无效',invalid_spread:'当日价差无效',invalid_imbalance:'当日盘口失衡无效'};
const TOLERANCE=1e-12;
let index=null,shard=null,events=[],filtered=[],selected=null,page=0,toastTimer=null,loadVersion=0;
const shardCache=new Map();
const controls=['attr-date','attr-direction','attr-threshold','attr-industry','attr-search','attr-sort','attr-reset'];

function status(message,error=false){$('attr-status').textContent=message;$('attr-status').hidden=!message;$('attr-status').dataset.error=String(error);$('attr-status').setAttribute('role',error?'alert':'status');$('attr-retry').hidden=!error;}
function notify(message){$('attr-toast').textContent=message;$('attr-toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('attr-toast').hidden=true,4500);}
async function fetchJSON(path){const response=await fetch(new URL(`../${path}`,import.meta.url));if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json();}
const stockName=symbol=>index?.stocks?.[symbol]?.name||'';
const stockIndustry=symbol=>index?.stocks?.[symbol]?.industry||'';
const featureLabel=feature=>feature==='flow'?'成交与盘口交易本身（未被公开信息覆盖的部分）':(index?.feature_labels?.[feature]||index?.flow_labels?.[feature]||feature);
const familyLabel=family=>index?.families?.[family]?.label||family;
const windowLabel=window=>index?.window_labels?.[window]||window;
const calibrationKey=event=>`${event.r<0?'down':'up'}:${event.t}`;
const topCause=event=>event.causes?.length?Math.max(...event.causes.map(c=>c.p||0)):0;

export function filterEvents(rows,{direction='down',threshold=0.05,industry='',search='',sort='magnitude'}={},names={}){
  const query=String(search).trim().toLowerCase(),limit=Number(threshold);
  const out=rows.filter(event=>finite(event.r)&&Math.abs(event.r)>limit+TOLERANCE&&(direction==='all'||(direction==='down'?event.r<0:event.r>0))&&(!industry||event.industry===industry)&&(!query||String(event.symbol).includes(query)||String(names[event.symbol]||'').toLowerCase().includes(query)));
  const bySymbol=(a,b)=>String(a.symbol).localeCompare(String(b.symbol));
  if(sort==='specific')out.sort((a,b)=>(b.shares?.[2]??0)-(a.shares?.[2]??0)||Math.abs(b.r)-Math.abs(a.r)||bySymbol(a,b));
  else if(sort==='top_cause')out.sort((a,b)=>topCause(b)-topCause(a)||Math.abs(b.r)-Math.abs(a.r)||bySymbol(a,b));
  else if(sort==='symbol')out.sort(bySymbol);
  else out.sort((a,b)=>Math.abs(b.r)-Math.abs(a.r)||bySymbol(a,b));
  return out;
}
function currentFilters(){return {direction:$('attr-direction').value,threshold:$('attr-threshold').value,industry:$('attr-industry').value,search:$('attr-search').value,sort:$('attr-sort').value};}

function renderList(){
  const pages=Math.ceil(filtered.length/PAGE_SIZE);page=Math.max(0,Math.min(page,Math.max(0,pages-1)));
  $('attr-event-count').textContent=`${number(filtered.length,0)} 个事件`;
  $('attr-page').textContent=pages?`${page+1} / ${number(pages,0)}`:'0 / 0';
  $('attr-prev').disabled=page===0||!pages;$('attr-next').disabled=page+1>=pages;
  const visible=filtered.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE);
  $('attr-event-list').innerHTML=visible.length?visible.map(event=>{
    const active=selected&&selected.id===event.id,up=event.r>0,shares=event.shares||[0,0,1];
    return `<button type="button" class="event-item" data-event-id="${escapeText(event.id)}" aria-pressed="${Boolean(active)}" aria-label="${escapeText(event.symbol)} ${escapeText(stockName(event.symbol))}，${dateText(event.date)}，${up?'上涨':'下跌'} ${magnitude(event.r)}"><strong>${escapeText(event.symbol)}</strong><span class="event-change ${up?'up':''}">${percent(event.r)}</span><span class="event-name">${escapeText(stockName(event.symbol)||'—')}${event.industry?` · ${escapeText(event.industry)}`:''}</span><span class="event-state ${event.screened?'':'flagged'}">${event.screened?'通过筛查':'口径待核查'}</span><span class="event-mini" aria-hidden="true"><i class="share-market" style="width:${shares[0]*100}%"></i><i class="share-industry" style="width:${shares[1]*100}%"></i><i class="share-specific" style="width:${shares[2]*100}%"></i></span></button>`;
  }).join(''):'<div class="ledger-empty"><strong>没有符合条件的事件</strong>可放宽阈值、切换方向或行业，或点击“重置”。</div>';
}

function renderEmpty(){
  $('attr-selection').innerHTML='<div><span class="section-label">所选事件</span><h2 id="attr-selection-heading">没有匹配事件</h2><p>调整左侧条件后继续查看归因。</p></div>';
  $('attr-quality').hidden=true;$('attr-share-bar').innerHTML='';$('attr-share-bar').setAttribute('aria-label','没有匹配事件');$('attr-decomp-table').innerHTML='';
  $('attr-causes').innerHTML='<li class="evidence-waiting">当前筛选结果为空。</li>';$('attr-evidence').innerHTML='<p class="evidence-waiting">当前筛选结果为空。</p>';
  $('attr-availability').textContent='未选择事件。';$('attr-export').disabled=true;
}

function causeNote(cause,event){
  const table=index.calibration?.[calibrationKey(event)],base=table?.base_rate,direction=event.r<0?'下跌':'上涨',threshold=`${number(event.t*100,0)}%`;
  if(cause.f==='flow'){
    const signals=cause.signals||[];
    const listed=signals.length?`<ul class="signal-list">${signals.map(s=>`<li>${escapeText(featureLabel(s.f))}${finite(s.lr)?`（似然比 ${number(s.lr,2)}）`:''}</li>`).join('')}</ul>`:'';
    return `${signals.length?'当日成交与盘口出现以下异常，属于交易本身的证据：':'未发现公开信息或明显的成交盘口异常；按本方法，剩余部分归于交易本身。'}${listed}`;
  }
  if(!finite(cause.lr))return '样本中没有足够观察用于校准。';
  return `样本中出现该证据的股票日，${share(cause.er)} 当日${direction}超过 ${threshold}${finite(base)?`（无该证据时约 ${share(base)}）`:''}；似然比 ${number(cause.lr,2)}，${number(cause.n,0)} 个异动日出现过该证据。`;
}

function renderSelection(){
  const e=selected,up=e.r>0,name=stockName(e.symbol);
  $('attr-selection').innerHTML=`<div><span class="section-label">所选日事件</span><h2 id="attr-selection-heading">${escapeText(e.symbol)}<small>${escapeText(name||'名称未知')}${e.industry?` · ${escapeText(e.industry)}`:''} · ${dateText(e.date)}</small></h2><p><span class="screen-badge ${e.screened?'':'flagged'}">${e.screened?'通过口径筛查':'口径待核查'}</span> 当日末价 ${number(e.price)}，参考前收 ${number(e.prev_close)}</p></div><div class="selected-quote"><span>当日${up?'涨幅':'跌幅'}</span><strong class="${up?'up':''}">${percent(e.r)}</strong><small>当日末价 / 参考前收 − 1</small></div>`;
  const flags=Array.isArray(e.flags)?e.flags:[];
  $('attr-quality').hidden=!flags.length&&e.screened;
  if(!$('attr-quality').hidden){$('attr-quality').className=`quality-note ${e.screened?'':'flagged'}`;$('attr-quality').innerHTML=`<strong>${e.screened?'口径提示':'该事件未进入筛查后基准'}</strong><ul>${flags.map(flag=>`<li>${escapeText(fallbackFlags[flag]||flag)}</li>`).join('')}</ul>`;}
}

function renderDecomposition(){
  const e=selected,[market,industry,specific]=e.shares||[0,0,1],up=e.r>0,word=up?'上涨':'下跌';
  $('attr-share-bar').innerHTML=[['share-market',market,'市场'],['share-industry',industry,'行业'],['share-specific',specific,'个股特有']].map(([cls,value,label])=>`<span class="${cls}" style="width:${Math.max(0,value*100)}%" title="${label} ${share(value)}">${value>=.12?`${label} ${share(value)}`:''}</span>`).join('');
  $('attr-share-bar').setAttribute('aria-label',`收益分解：市场共同因素 ${share(market)}，行业共同因素 ${share(industry)}，个股特有 ${share(specific)}`);
  const industryText=finite(e.i)?`${percent(e.i)}<small>${escapeText(e.industry||'行业')}同日中位数，${number(e.peers,0)} 只同行（剔除本股）</small>`:`不适用<small>${e.industry?'行业样本不足 8 只':'无行业标签'}</small>`;
  $('attr-decomp-table').innerHTML=`<div class="market"><dt>市场共同因素 ${share(market)}</dt><dd>${percent(e.m)}<small>同日筛查后样本收益中位数（剔除本股）；样本跌逾 5% 占比 ${share(e.breadth)}</small></dd></div><div class="industry"><dt>行业共同因素 ${share(industry)}</dt><dd>${industryText}</dd></div><div class="specific"><dt>个股特有部分 ${share(specific)}</dt><dd>${percent(e.r*specific)}<small>个股收益中未被同向市场与行业部分覆盖的幅度；以下原因只分配这一部分</small></dd></div>`;
  $('attr-decomp-caption').textContent=`${stockName(e.symbol)||e.symbol} ${dateText(e.date)} ${word} ${magnitude(e.r)}：市场 ${share(market)}、行业 ${share(industry)}、个股特有 ${share(specific)}`;
}

function renderCauses(){
  const e=selected,causes=e.causes||[];
  $('attr-causes').innerHTML=causes.length?causes.map(cause=>`<li class="cause-item ${cause.f==='flow'?'flow':''}"><span class="cause-rank" aria-hidden="true"></span><div><div class="cause-label">${escapeText(featureLabel(cause.f))}</div><div class="cause-note">${causeNote(cause,e)}</div></div><div class="cause-prob"><strong>${share(cause.p)}</strong><div class="cause-track"><i style="width:${Math.max(0,(cause.p||0)*100)}%"></i></div></div></li>`).join(''):'<li class="evidence-waiting">没有可分配的原因。</li>';
}

const resolveNews=refs=>(refs||[]).map(ref=>({...(shard?.news?.[String(ref.id)]||{title:'（文章记录缺失）'}),...ref}));
const COLLAPSED=3;
function itemRows(items,kind){
  if(!items?.length)return '<p class="empty-note">该窗口内没有记录。</p>';
  const rows=items.map((item,index)=>{
    const link=item.url?`<a href="${escapeText(item.url)}" target="_blank" rel="noopener noreferrer">${escapeText(item.title)}</a>`:escapeText(item.title);
    const tags=[`<span class="tag window-${escapeText(item.window)}">${escapeText(windowLabel(item.window))}</span>`];
    if(kind==='anno')tags.push(`<span class="tag">${escapeText(familyLabel(item.family))}</span>`,finite(item.direction)&&item.direction!==0?`<span class="tag ${item.direction<0?'dir-neg':'dir-pos'}">${item.direction<0?'偏负面':'偏正面'}</span>`:'');
    if(kind==='news')tags.push(item.source?`<span class="tag">${escapeText(item.source)}</span>`:'',item.regulator?'<span class="tag">监管或政策</span>':'');
    return `<li${index>=COLLAPSED?' data-extra hidden':''}><span class="item-time">${timeText(item.time)}</span><span class="item-title">${link}</span><span class="item-tags">${tags.join('')}</span></li>`;
  }).join('');
  const toggle=items.length>COLLAPSED?`<button type="button" class="evidence-toggle text-button" data-total="${items.length}" aria-expanded="false">展开全部 ${items.length} 条</button>`:'';
  return `<ul class="item-list">${rows}</ul>${toggle}`;
}
function toggleEvidence(button){
  const expanded=button.getAttribute('aria-expanded')!=='true';
  button.previousElementSibling.querySelectorAll('li[data-extra]').forEach(li=>{li.hidden=!expanded;});
  button.setAttribute('aria-expanded',String(expanded));button.textContent=expanded?`收起，只显示前 ${COLLAPSED} 条`:`展开全部 ${button.dataset.total} 条`;
}
$('attr-evidence').addEventListener('click',event=>{const button=event.target.closest('.evidence-toggle');if(button)toggleEvidence(button);});

function renderEvidence(){
  const e=selected,ev=e.ev||{},flow=e.flow||{};
  const macro=shard?.macro_news||[];
  const flowRows=[['成交量 / 前 5 日中位数',ratio(e.vr)],['平均价差 / 前 5 日中位数',ratio(e.sr)],['盘口绝对失衡变化',finite(e.ic)?`${e.ic>0?'+':''}${number(e.ic*100,2)} 百分点`:'历史不足']];
  const mf=e.mf||{},mfShare=value=>finite(value)&&finite(mf.turnoverValue)&&mf.turnoverValue>0?`${compact(value)} 元（占成交额 ${(value/mf.turnoverValue*100).toFixed(1)}%）`:compact(value)+(finite(value)?' 元':'');
  if(e.mf){flowRows.push(['主力（大单+超大单）净流入',mfShare(mf.mainFlow)],['超大单净流入',mfShare(mf.netFlowXL)],['大单净流入',mfShare(mf.netFlowL)],['中小单净流入',mfShare(mf.smainFlow)],['全部净流入',mfShare(mf.netFlow)],['当日成交额',finite(mf.turnoverValue)?`${compact(mf.turnoverValue)} 元`:'—'],['成交笔数',whole(mf.dealAmount)]);}
  if(Object.keys(flow).length){flowRows.push(['开盘跳空',percent(flow.gap_return)],['最大 5 分钟跌幅',`${percent(flow.min_bar_return)}（${barTime(flow.min_bar_at)}）`],['最大 5 分钟涨幅',`${percent(flow.max_bar_return)}（${barTime(flow.max_bar_at)}）`],['日内最大回撤',`${percent(flow.max_drawdown)}（${barTime(flow.max_drawdown_at)}）`],['主动买入减卖出占比',finite(flow.net_buy_share)?`${flow.net_buy_share>0?'+':''}${number(flow.net_buy_share*100,1)} 百分点`:'—'],['大额成交占成交额',share(flow.big_print_share)],['笔均成交额',compact(flow.avg_trade_value)],['触及跌停快照数',number(flow.at_low_limit_snaps,0)],['跌停封单最大量',compact(flow.low_limit_queue_max)],['触及涨停快照数',number(flow.at_high_limit_snaps,0)]);}
  const contrary=(e.contrary||[]).map(f=>`<li>${escapeText(featureLabel(f))}</li>`).join('');
  const weak=(e.weak||[]).map(f=>`<li>${escapeText(featureLabel(f))}</li>`).join('');
  const explain=(e.explain||[]).map(f=>`<li>${escapeText(featureLabel(f))}</li>`).join('');
  $('attr-evidence').innerHTML=`<div class="evidence-block"><h4>本公司公告<small>${number((ev.announcements||[]).length,0)} 条，按窗口标注</small></h4>${itemRows(ev.announcements,'anno')}</div>
  <div class="evidence-block"><h4>直接提及本股的财经文章<small>公众号来源，仅标题与链接</small></h4>${itemRows(resolveNews(ev.news_company),'news')}</div>
  <div class="evidence-block"><h4>本行业报道<small>${escapeText(e.industry||'无行业标签')}</small></h4>${itemRows(resolveNews(ev.news_industry),'news')}</div>
  <div class="evidence-block"><h4>当日宏观、政策与市场背景<small>同日全市场共享</small></h4>${itemRows(macro.map(item=>({...item,window:'pre_open'})),'news')}</div>
  <div class="evidence-block"><h4>成交、资金流向与盘口<small>日汇总、供应商资金流向拆分与快照代理指标</small></h4><dl class="flow-grid">${flowRows.map(([k,v])=>`<dt>${escapeText(k)}</dt><dd>${v}</dd>`).join('')}</dl>${e.mf?'<p class="empty-note">资金流向按单笔成交金额分为小单、中单、大单、超大单；主力 = 大单 + 超大单。它描述成交单的大小，不识别交易者身份。</p>':''}</div>
  <div class="evidence-block"><h4>未计入原因的证据<small>反向、关联较弱或事后披露</small></h4>${contrary?`<p class="empty-note">与本次方向相反的信息：</p><ul class="limitation-list">${contrary}</ul>`:''}${weak?`<p class="empty-note">出现但样本关联较弱（似然比低于 ${number(index.metadata?.min_lr??1.5,1)}）：</p><ul class="limitation-list">${weak}</ul>`:''}${explain?`<p class="empty-note">当日收盘后的事后披露：</p><ul class="limitation-list">${explain}</ul>`:''}${!contrary&&!weak&&!explain?'<p class="empty-note">没有反向、弱关联或事后披露记录。</p>':''}</div>`;
  $('attr-availability').textContent=`日度证据可用时间：${dateTime(shard?.available_at)}。事件标识 ${e.id}。公告与文章时间为来源发布时间（上海时间）。`;
}

function exportEvent(){
  if(!selected)return;
  const payload={schema_version:1,kind:'daily_event_attribution',in_sample_association:true,simulated:false,event:selected,news_items:{company:resolveNews(selected.ev?.news_company),industry:resolveNews(selected.ev?.news_industry)},macro_news:shard?.macro_news||[],labels:{features:Object.fromEntries((selected.causes||[]).map(c=>[c.f,featureLabel(c.f)])),families:index.families,windows:index.window_labels},calibration:index.calibration?.[calibrationKey(selected)],metadata:{version:index.metadata?.version,builder_sha256:index.metadata?.builder_sha256,limitations:index.metadata?.limitations}};
  const blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=`attribution_${selected.id}.json`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('已导出本事件的归因记录。');
}

function selectEvent(id){
  const event=events.find(row=>row.id===String(id));if(!event)return;
  selected=event;renderList();renderSelection();renderDecomposition();renderCauses();renderEvidence();$('attr-export').disabled=false;
}

function applyFilters(){
  if(!shard)return;page=0;filtered=filterEvents(events,currentFilters(),Object.fromEntries(Object.entries(index.stocks||{}).map(([k,v])=>[k,v.name])));
  const t=Number($('attr-threshold').value),d=$('attr-direction').value;
  $('attr-rule-chip').textContent=`${d==='all'?'涨跌':d==='down'?'下跌':'上涨'}严格超过 ${number(t*100,0)}%`;
  if(!filtered.length){selected=null;renderList();renderEmpty();return;}
  selectEvent(filtered[0].id);
}

const LEVEL_FILES={'0.05':['0.05'],'0.03':['0.03','0.05'],'0.02':['0.02','0.03','0.05']};
async function loadDate(date){
  const version=++loadVersion;status(`正在载入 ${dateText(date)} 的事件…`);
  try{
    const manifest=index.shards?.[String(date)]?.levels||{};
    const files=(LEVEL_FILES[$('attr-threshold').value]||LEVEL_FILES['0.05']).map(level=>manifest[level]?.file||`data/attribution/events/${date}-${Math.round(Number(level)*100)}.json`);
    for(const file of files)if(!shardCache.has(file))shardCache.set(file,fetchJSON(file).catch(error=>{shardCache.delete(file);throw error;}));
    const parts=await Promise.all(files.map(file=>shardCache.get(file)));if(version!==loadVersion)return;
    if(parts.some(part=>part.schema_version!==1||!Array.isArray(part.events)||String(part.date)!==String(date)))throw Error('分片格式无效');
    shard={date:parts[0].date,available_at:parts[0].available_at,market:parts[0].market,macro_news:parts[0].macro_news||[],news:Object.assign({},...parts.map(part=>part.news||{})),events:parts.flatMap(part=>part.events)};
    events=shard.events;
    for(const event of events){event.industry=stockIndustry(event.symbol);event.name=stockName(event.symbol);}
    const industries=new Map();for(const event of events)if(event.industry)industries.set(event.industry,(industries.get(event.industry)||0)+1);
    const current=$('attr-industry').value;
    $('attr-industry').innerHTML='<option value="">全部行业</option>'+[...industries.entries()].sort((a,b)=>b[1]-a[1]).map(([name,count])=>`<option value="${escapeText(name)}">${escapeText(name)}（${count}）</option>`).join('');
    if([...industries.keys()].includes(current))$('attr-industry').value=current;
    status('');applyFilters();
  }catch(error){if(version!==loadVersion)return;status(`事件分片加载失败（${error.message}）。请选择“重新加载”重试。`,true);}
}

function resetFilters(){$('attr-direction').value='down';$('attr-threshold').value='0.05';$('attr-industry').value='';$('attr-search').value='';$('attr-sort').value='magnitude';loadDate($('attr-date').value);}

async function initialize(){
  status('正在载入归因索引…');$('attr-retry').hidden=true;controls.forEach(id=>$(id).disabled=true);
  try{
    const result=await fetchJSON('data/attribution/index.json');
    if(result.schema_version!==1||!result.summary||!result.shards)throw Error('索引格式无效');
    index=result;
    const summary=index.summary,counts=index.metadata?.evidence_counts||{};
    $('attr-stock-total').textContent=number(Object.keys(index.stocks||{}).length,0);$('attr-date-total').textContent=number((summary.dates||[]).length,0);
    $('attr-event-total').textContent=number(summary.events_total,0);$('attr-anno-total').textContent=number(counts.announcements,0);$('attr-news-total').textContent=number(counts.news,0);
    const dates=[...(summary.dates||[])].sort((a,b)=>b-a);
    $('attr-date').innerHTML=dates.map(date=>`<option value="${date}">${dateText(date)}（${number(index.shards?.[String(date)]?.events,0)}）</option>`).join('');
    const population=summary.population,cal=index.calibration?.['down:0.05'];
    $('attr-calibration-note').textContent=`校准样本：${number(population,0)} 个筛查后股票日；其中严格下跌超过 5% 的 ${number(cal?.events,0)} 个，基准发生率 ${share(cal?.base_rate)}。`;
    $('attr-limitations').innerHTML=(index.metadata?.limitations||[]).map(text=>`<li>${escapeText(text)}</li>`).join('');
    controls.forEach(id=>$(id).disabled=false);
    await loadDate(dates[0]);
  }catch(error){status(`归因索引加载失败（${error.message}）。请选择“重新加载”重试。`,true);}
}

$('attr-event-list').addEventListener('click',event=>{const button=event.target.closest('[data-event-id]');if(button)selectEvent(button.dataset.eventId);});
$('attr-event-list').addEventListener('keydown',event=>{
  if(!['ArrowDown','ArrowUp'].includes(event.key)||!selected)return;event.preventDefault();
  const position=filtered.findIndex(row=>row.id===selected.id),next=filtered[position+(event.key==='ArrowDown'?1:-1)];
  if(!next)return;page=Math.floor(filtered.indexOf(next)/PAGE_SIZE);selectEvent(next.id);$('attr-event-list').querySelector(`[data-event-id="${CSS.escape(next.id)}"]`)?.focus();
});
$('attr-prev').addEventListener('click',()=>{page--;renderList();});$('attr-next').addEventListener('click',()=>{page++;renderList();});
$('attr-date').addEventListener('change',()=>loadDate($('attr-date').value));
$('attr-threshold').addEventListener('change',()=>loadDate($('attr-date').value));
for(const id of ['attr-direction','attr-industry','attr-sort'])$(id).addEventListener('change',applyFilters);
$('attr-search').addEventListener('input',applyFilters);
$('attr-reset').addEventListener('click',resetFilters);
$('attr-export').addEventListener('click',()=>{try{exportEvent();}catch(error){notify(error.message);}});
$('attr-retry').addEventListener('click',()=>index?loadDate($('attr-date').value):initialize());
window.attributionCauses={get index(){return index;},get shard(){return shard;},get events(){return events;},get filtered(){return filtered;},get selected(){return selected;},selectEvent,loadDate,filterEvents,exportPayload:()=>selected?{event:selected,calibration:index?.calibration?.[calibrationKey(selected)]}:null};
initialize();
