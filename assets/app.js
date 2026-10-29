import {AttributionReplay} from './engine.js?v=20261028-2';

const $=id=>document.getElementById(id);
const escapeText=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateText=value=>String(value).replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3');
const timeText=seconds=>{const n=Math.floor(Number(seconds)||0);return [Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(v=>String(v).padStart(2,'0')).join(':');};
const pct=value=>Number.isFinite(value)?`${value>0?'+':''}${(value*100).toFixed(2)}%`:'—';
const price=value=>Number.isFinite(value)?value.toFixed(2):'—';
const ratio=value=>Number.isFinite(value)?`${value.toFixed(2)}×`:'—';
const whole=value=>Number.isFinite(value)?Math.round(value).toLocaleString('zh-CN'):'—';
const movement=value=>!Number.isFinite(value)||value===0?'neutral':value<0?'negative':'positive';
const basisText=basis=>basis==='day_return'?'较参考前收盘价':'较此前收盘峰值';
const barSeconds=b=>b.seconds??b.end_seconds??((Number(b.session)===1?46800:34200)+(Number(b.bar)+1)*300);
const stamp=iso=>iso?String(iso).slice(5,16).replace('T',' '):'—';
let engine=null,evidencePack=null,selectedSymbol='000011',selectedEvent=null,activePage='monitor',popupQueue=[],popupEvent=null,toastTimer=null,lastChartKey='';
const emptyEventSummary=$('selected-event-summary').innerHTML,emptyReasons=$('reason-list').innerHTML,emptyEvidence=$('evidence-list').innerHTML;
const windowLabel=w=>evidencePack?.metadata?.windows?.[w]||w;
function clearEventDetails(){selectedEvent=null;$('selected-event-summary').innerHTML=emptyEventSummary;$('reason-list').innerHTML=emptyReasons;$('evidence-list').innerHTML=emptyEvidence;}

function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5000);}
function setPage(page){if(!['monitor','history','method'].includes(page))return;activePage=page;document.querySelectorAll('.page').forEach(el=>{el.hidden=el.id!==`page-${page}`;el.classList.toggle('active',!el.hidden);});document.querySelectorAll('.nav-button').forEach(el=>{const current=el.dataset.page===page;el.classList.toggle('active',current);if(current)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});if(page==='history')renderHistory();if(page==='monitor'&&engine)render(true);history.replaceState(null,'',`#${page}`);window.scrollTo({top:0,behavior:'instant'});}
document.querySelectorAll('[data-page]').forEach(button=>button.addEventListener('click',()=>setPage(button.dataset.page)));
$('open-method').addEventListener('click',event=>{event.preventDefault();setPage('method');});
document.querySelector('.brand').addEventListener('click',event=>{event.preventDefault();setPage('monitor');});

function reasonNote(reason,event){
  const attribution=event.evidence?.attribution;
  if(reason.flow){const signals=reason.signals||[];return signals.length?`当日成交与盘口的异常信号：${signals.map(s=>escapeText(s.label)).join('；')}。`:'未发现可信的公开信息，也没有明显的成交盘口异常；按方法剩余部分归于交易本身。';}
  const level=attribution?`${Math.round(attribution.t*100)}%`:'阈值';const direction=attribution&&attribution.r<0?'下跌':'上涨';
  if(!Number.isFinite(reason.likelihood_ratio))return '样本中没有足够观察用于校准。';
  return `样本中出现该证据的股票日，${(reason.event_rate*100).toFixed(1)}% 当日${direction}超过 ${level}；似然比 ${reason.likelihood_ratio.toFixed(2)}，${whole(reason.support)} 个异动日出现过该证据。`;
}
function reasonsHTML(event){
  const ev=event.evidence||{};
  if(!ev.available)return '<div class="reason-empty"><strong>证据包未载入</strong>该股票日没有可用的归因与证据记录。</div>';
  if(!event.reasons?.length)return `<div class="reason-empty"><strong>日终未构成归因事件</strong>当日收盘涨跌幅 ${pct(ev.daily_return)}（样本中位数 ${pct(ev.market_return)}），未超过 2% 的日度阈值，因此没有原因概率；下方证据仍按发布时间列出。</div>`;
  const shares=ev.attribution?.shares||[0,0,1];
  const head=`<div class="share-line"><span>日终收益 ${pct(ev.attribution.r)}</span><span>市场 ${Math.round(shares[0]*100)}%</span><span>行业 ${Math.round(shares[1]*100)}%</span><span>个股特有 ${Math.round(shares[2]*100)}%</span></div>`;
  return head+event.reasons.map((reason,index)=>`<article class="reason-item ${reason.flow?'flow':''}"><div class="reason-title-line"><h3><span class="reason-rank">${String(index+1).padStart(2,'0')}</span>${escapeText(reason.title)}</h3><div class="reason-probability"><strong>${reason.probability.toFixed(1)}<small>%</small></strong><small>概率</small></div></div><div class="probability-track" aria-hidden="true"><span style="width:${Math.max(0,Math.min(100,reason.probability))}%"></span></div><p class="reason-description">${reasonNote(reason,event)}</p><span class="reason-category">日终归因 · 样本内经验关联</span></article>`).join('');
}
const COLLAPSED=3;
function itemList(items,kind){
  if(!items?.length)return '<p class="evidence-none">无</p>';
  const rows=items.map((item,index)=>{const link=item.url?` <a href="${escapeText(item.url)}" target="_blank" rel="noopener noreferrer">原文 ↗</a>`:'';const tags=kind==='anno'?`<em>${escapeText(item.label)}</em><em>${escapeText(windowLabel(item.window))}</em>`:`${item.source?`<em>${escapeText(item.source)}</em>`:''}${item.regulator?'<em>监管或政策</em>':''}`;return `<li${index>=COLLAPSED?' data-extra hidden':''}><time>${stamp(item.time)}</time><span>${escapeText(item.title)}${link}</span><span class="evidence-tags">${tags}</span></li>`;}).join('');
  const toggle=items.length>COLLAPSED?`<button type="button" class="evidence-toggle" data-total="${items.length}" aria-expanded="false">展开全部 ${items.length} 条</button>`:'';
  return `<ul class="evidence-items">${rows}</ul>${toggle}`;
}
function toggleEvidence(button){
  const expanded=button.getAttribute('aria-expanded')!=='true';
  button.previousElementSibling.querySelectorAll('li[data-extra]').forEach(li=>{li.hidden=!expanded;});
  button.setAttribute('aria-expanded',String(expanded));
  button.textContent=expanded?`收起，只显示前 ${COLLAPSED} 条`:`展开全部 ${button.dataset.total} 条`;
}
document.addEventListener('click',event=>{const button=event.target.closest('.evidence-toggle');if(button)toggleEvidence(button);});
function flowHTML(event){
  const i=event.intraday||{},d=event.evidence?.flow||{},rows=[];
  if(event.intraday){rows.push(['触发 K 线成交量 / 当日此前 K 线中位数',ratio(i.volume_multiple)],['触发 K 线涨跌幅',pct(i.bar_return)],['较当日收盘峰值回撤',pct(i.drop_from_peak)],['连续下跌 K 线数',whole(i.down_bar_streak)],['触发 K 线占当日累计成交量',Number.isFinite(i.bar_share_of_session)?`${(i.bar_share_of_session*100).toFixed(1)}%`:'—']);}
  if(event.evidence?.flow){rows.push(['当日成交量 / 前 5 日中位数（日终）',ratio(d.volume_ratio)],['当日平均价差 / 前 5 日中位数（日终）',ratio(d.spread_ratio)],['盘口绝对失衡变化（日终）',Number.isFinite(d.imbalance_change)?`${d.imbalance_change>0?'+':''}${(d.imbalance_change*100).toFixed(2)} 百分点`:'—']);}
  const mf=event.evidence?.moneyflow;
  if(mf){const money=v=>Number.isFinite(v)?`${Math.abs(v)>=1e8?(v/1e8).toFixed(2)+' 亿':(v/1e4).toFixed(0)+' 万'} 元${Number.isFinite(mf.turnoverValue)&&mf.turnoverValue>0?`（占成交额 ${(v/mf.turnoverValue*100).toFixed(1)}%）`:''}`:'—';rows.push(['主力（大单+超大单）净流入（日终）',money(mf.mainFlow)],['超大单净流入（日终）',money(mf.netFlowXL)],['大单净流入（日终）',money(mf.netFlowL)],['中小单净流入（日终）',money(mf.smainFlow)],['当日成交额',Number.isFinite(mf.turnoverValue)?`${(mf.turnoverValue/1e8).toFixed(2)} 亿元`:'—']);}
  return rows.length?`<dl class="flow-facts">${rows.map(([k,v])=>`<dt>${escapeText(k)}</dt><dd>${v}</dd>`).join('')}</dl>${mf?'<p class="evidence-none">资金流向按单笔成交金额分为小、中、大、超大单，主力 = 大单 + 超大单；描述成交单大小，不识别交易者身份。</p>':''}`:'<p class="evidence-none">无</p>';
}
function evidenceHTML(event,compact=false){
  const ev=event.evidence||{};
  if(!ev.available)return '<p class="evidence-none">证据包未载入。</p>';
  const known=ev.known,later=ev.later;
  return `<section class="evidence-group"><h4>事件前已知<small>截至 ${escapeText(event.time)}，含此前 5 个交易日；每类默认显示前 ${COLLAPSED} 条，可展开全部</small></h4>
    <h5>本公司公告 <b>${known.announcements.length}</b></h5>${itemList(known.announcements,'anno')}
    <h5>直接提及本股的财经文章 <b>${known.news_company.length}</b></h5>${itemList(known.news_company,'news')}
    <h5>${escapeText(ev.industry||'行业')}行业报道 <b>${known.news_industry.length}</b></h5>${itemList(known.news_industry,'news')}
    <h5>宏观、政策与市场背景 <b>${known.macro.length}</b></h5>${itemList(known.macro,'news')}</section>
  <section class="evidence-group later"><h4>事后披露与后续报道<small>事件时点之后发布，不计入原因</small></h4>
    <h5>本公司公告 <b>${later.announcements.length}</b></h5>${itemList(later.announcements,'anno')}
    <h5>本股与行业报道 <b>${later.news_company.length+later.news_industry.length}</b></h5>${itemList([...later.news_company,...later.news_industry].sort((a,b)=>String(a.time).localeCompare(String(b.time))),'news')}</section>
  <section class="evidence-group flow"><h4>盘口与成交变化<small>五分钟 K 线与日终汇总的实际数值</small></h4>${flowHTML(event)}</section>`;
}
function summaryHTML(event){const ev=event.evidence||{};return `<div class="event-meta-line"><strong>${escapeText(event.symbol)}</strong><span>${pct(event.change)}</span></div><span class="event-timing">${escapeText(ev.name||'')}${ev.industry?` · ${escapeText(ev.industry)}`:''} · ${dateText(event.date)} ${escapeText(event.time??timeText(event.seconds))} · ${basisText(event.basis)}</span><p>触发价 ${price(event.price)}，参考价 ${price(event.reference)}。原因按日终归因概率排序，证据按发布时间标注。</p>`;}
function showEventDetails(event){selectedEvent=event;$('selected-event-summary').innerHTML=summaryHTML(event);$('reason-list').innerHTML=reasonsHTML(event);$('evidence-list').innerHTML=evidenceHTML(event,false);}
function showPopup(event){popupEvent=event;showEventDetails(event);$('popup-title').textContent=`${event.symbol} ${event.evidence?.name||''} 下跌超过 5%`;$('popup-meta').innerHTML=`<span>${dateText(event.date)} ${escapeText(event.time??timeText(event.seconds))}</span><span>${basisText(event.basis)}</span><strong class="negative">${pct(event.change)}</strong>`;$('popup-reasons').innerHTML=reasonsHTML(event);$('popup-evidence').innerHTML=evidenceHTML(event,true);$('popup-queue').textContent=popupQueue.length?`另有 ${popupQueue.length} 个新触发事件待查看`:'回放已暂停，便于查看';$('review-event').textContent=popupQueue.length?'查看下一个事件':'在工作台查看';if(!$('event-dialog').open)$('event-dialog').showModal();$('event-dialog').scrollTop=0;}
function onEvents(events){if(!events?.length)return;engine.pause();popupQueue.push(...events);const first=popupQueue.shift();selectedSymbol=first.symbol;$('symbol-search').value='';showPopup(first);render(true);}
$('review-event').addEventListener('click',()=>{if(popupQueue.length){const next=popupQueue.shift();selectedSymbol=next.symbol;showPopup(next);render(true);return;}selectedSymbol=popupEvent.symbol;if(engine.state.date!==popupEvent.date)engine.setDate(popupEvent.date);if(engine.state.basis!==popupEvent.basis)engine.setBasis(popupEvent.basis);if(Math.floor(engine.state.cursor)!==popupEvent.seconds)engine.seek(popupEvent.seconds);$('symbol-search').value='';$('event-dialog').close();setPage('monitor');render(true);});
$('close-dialog').addEventListener('click',()=>{$('event-dialog').close();popupQueue=[];});
$('event-dialog').addEventListener('cancel',()=>{popupQueue=[];});

function renderStockTape(){const query=$('symbol-search').value.trim();const symbols=engine.data.symbols.filter(symbol=>String(symbol).includes(query));$('stock-tape').innerHTML=symbols.length?symbols.map(symbol=>{const quote=engine.priceState(symbol);return `<button class="stock-button ${symbol===selectedSymbol?'selected':''}" data-symbol="${escapeText(symbol)}" aria-pressed="${symbol===selectedSymbol}"><strong>${escapeText(symbol)}</strong><span class="${movement(quote.change)}">${pct(quote.change)}</span></button>`;}).join(''):'<p class="empty-state">八只样本中未找到该代码。</p>';$('stock-tape').querySelectorAll('[data-symbol]').forEach(button=>button.addEventListener('click',()=>{selectedSymbol=button.dataset.symbol;render(true);}));}
function renderChart(){const bars=engine.visibleBars(selectedSymbol);const q=engine.priceState(selectedSymbol);$('selected-symbol').textContent=`${selectedSymbol}${evidencePack?.symbols?.[selectedSymbol]?.name?` ${evidencePack.symbols[selectedSymbol].name}`:''}`;$('quote-price').textContent=price(q.price);$('quote-price').className=movement(q.change);$('quote-change').textContent=pct(q.change);$('quote-change').className=`quote-change ${movement(q.change)}`;$('quote-reference').textContent=q.available?`${basisText(engine.state.basis)} ${price(q.reference)}`:bars.length?'当前 K 线尚无有效参考价格':'当前时点尚无已完成 K 线';$('chart-empty').hidden=bars.length>0;
  const W=Math.max(345,Math.min(1000,Math.round($('price-chart').parentElement.clientWidth)||1000)),H=W<500?280:W<700?340:410,L=W<500?42:54,R=18,T=22,priceH=H-145,volTop=H-107,volH=57,bottom=H-15,inner=W-L-R;
  $('price-chart').setAttribute('viewBox',`0 0 ${W} ${H}`);
  const lows=bars.map(b=>b.low),highs=bars.map(b=>b.high),threshold=q.available&&Number.isFinite(q.reference)&&q.reference>0?q.reference*.95:NaN;
  if(Number.isFinite(threshold)&&bars.length){lows.push(threshold);highs.push(threshold);}
  let min=lows.length?Math.min(...lows):0,max=highs.length?Math.max(...highs):1;const padding=Math.max((max-min)*.12,max*.0015,.01);min-=padding;max+=padding;
  const py=value=>T+(max-value)/(max-min)*priceH,x=slot=>L+(slot+.5)*inner/48,slot=b=>Number(b.session)*24+Number(b.bar),volumeMax=Math.max(...bars.map(b=>b.volume),1);
  const parts=[`<rect x="${L}" y="${T}" width="${inner}" height="${priceH}" fill="#fff"/>`];
  for(let i=0;i<=4;i++){const y=T+i*priceH/4,val=max-i*(max-min)/4;parts.push(`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="#e9edf4" stroke-width="1"/><text x="${L-10}" y="${y+4}" text-anchor="end" fill="#8995a7" font-size="12" font-family="Segoe UI, sans-serif">${val.toFixed(2)}</text>`);}
  const lunch=L+inner/2;parts.push(`<line x1="${lunch}" x2="${lunch}" y1="${T}" y2="${volTop+volH}" stroke="#dce2ed" stroke-dasharray="4 5"/><text x="${lunch}" y="${bottom}" text-anchor="middle" fill="#8995a7" font-size="11">午间休市</text><text x="${L}" y="${volTop-10}" fill="#8995a7" font-size="11">成交量</text>`);
  const candleW=Math.max(3,inner/48*.55);
  for(const b of bars){const cx=x(slot(b)),color=b.close>=b.open?'#c04d55':'#168276',y1=py(b.open),y2=py(b.close),vh=Math.max(0,b.volume)/volumeMax*volH;const title=`${timeText(barSeconds(b)).slice(0,5)} 开 ${price(b.open)} 高 ${price(b.high)} 低 ${price(b.low)} 收 ${price(b.close)} 成交量 ${Math.round(b.volume).toLocaleString('zh-CN')}`;parts.push(`<g><title>${escapeText(title)}</title><line x1="${cx}" x2="${cx}" y1="${py(b.high)}" y2="${py(b.low)}" stroke="${color}" stroke-width="1.3"/><rect x="${cx-candleW/2}" y="${Math.min(y1,y2)}" width="${candleW}" height="${Math.max(1.5,Math.abs(y2-y1))}" fill="${color}"/><rect x="${cx-candleW/2}" y="${volTop+volH-vh}" width="${candleW}" height="${vh}" fill="${color}" opacity=".48"/><rect x="${cx-inner/96}" y="${T}" width="${inner/48}" height="${volTop+volH-T}" fill="transparent"/></g>`);}
  if(Number.isFinite(threshold)&&bars.length){const y=py(threshold);parts.push(`<line x1="${L}" x2="${W-R}" y1="${y}" y2="${y}" stroke="#9290b6" stroke-width="1.1" stroke-dasharray="6 4"/><rect x="${W-R-133}" y="${y-15}" width="133" height="15" fill="#fff" fill-opacity=".92"/><text x="${W-R-4}" y="${y-4}" text-anchor="end" fill="#7f7aa5" font-size="11">−5% 阈值 ${price(threshold)}</text>`);}
  if(bars.length){const last=bars[bars.length-1],cx=x(slot(last));parts.push(`<line x1="${cx}" x2="${cx}" y1="${T}" y2="${volTop+volH}" stroke="#9aa6cb" stroke-dasharray="2 4" stroke-opacity=".55"/><circle cx="${cx}" cy="${py(last.close)}" r="3" fill="#4354b9" stroke="white" stroke-width="1"/>`);}
  for(const [s,label]of[[0,'09:35'],[11,'10:30'],[35,'14:00'],[47,'15:00']])parts.push(`<text x="${x(s)}" y="${bottom}" text-anchor="middle" fill="#8995a7" font-size="11">${label}</text>`);
  $('price-chart').innerHTML=parts.join('');$('price-chart').setAttribute('aria-label',`${selectedSymbol} ${dateText(engine.state.date)}，截至 ${timeText(engine.state.cursor)}，${bars.length} 根已完成 K 线，当前价格 ${price(q.price)}，跌幅 ${pct(q.change)}。`);
}
function eventRow(event){const top=event.reasons?.[0];const label=top?`${top.title}（${top.probability.toFixed(1)}%）`:event.evidence?.available?'日终未构成归因事件':'证据包未载入';const known=event.evidence?.available?event.evidence.known.announcements.length+event.evidence.known.news_company.length:0;return `<button class="event-row" data-event-id="${escapeText(event.id)}"><strong>${escapeText(event.symbol)}</strong><time>${dateText(event.date)} ${escapeText(event.time??timeText(event.seconds))}</time><span class="row-change">${pct(event.change)}</span><span class="row-reason">${escapeText(label)}<small>事件前已知公告与文章 ${known} 条</small></span><span class="row-action">查看原因与证据 ↗</span></button>`;}
function attachEventRows(container){container.querySelectorAll('[data-event-id]').forEach(button=>button.addEventListener('click',()=>{const event=engine.state.events.find(item=>String(item.id)===button.dataset.eventId);if(!event)return;engine.pause();showEventDetails(event);popupQueue=[];showPopup(event);render();}));}
function renderHistory(){if(!engine)return;$('history-total').textContent=`${engine.state.events.length} 个事件`;$('history-list').innerHTML=engine.state.events.length?[...engine.state.events].reverse().map(eventRow).join(''):'<p class="empty-state">暂无事件记录，返回工作台开始回放。</p>';attachEventRows($('history-list'));$('export').disabled=!engine.state.events.length;}
function render(force=false){if(!engine)return;const s=engine.state;$('clock').textContent=timeText(s.cursor);$('play-state').textContent=s.running?'播放中':'已暂停';$('play').textContent=s.running?'Ⅱ':'▶';$('play').setAttribute('aria-label',s.running?'暂停回放':'开始回放');$('seek').value=s.cursor;$('date-select').value=String(s.date);$('basis-select').value=s.basis;$('speed-select').value=String(s.speed);const key=`${s.date}/${Math.floor(s.cursor/300)}/${s.basis}/${selectedSymbol}/${s.events.length}`;
  if(force||key!==lastChartKey){lastChartKey=key;renderStockTape();renderChart();$('nav-count').textContent=s.events.length;$('current-count').textContent=s.currentEvents.length;$('recent-events').innerHTML=s.currentEvents.length?[...s.currentEvents].reverse().slice(0,5).map(eventRow).join(''):'<p class="empty-state">本轮尚未触发事件。点击“定位下一事件”，查看下一次阈值跨越。</p>';attachEventRows($('recent-events'));if(activePage==='history')renderHistory();$('observation').textContent=`同时监测 ${engine.data.symbols.length} 只股票，以完成的五分钟 K 线检查 ${basisText(s.basis)}是否严格低于 −5%。可定位下一次阈值跨越。`;}
}

$('symbol-search').addEventListener('input',()=>{if(engine)renderStockTape();});
$('play').addEventListener('click',()=>{engine.state.running?engine.pause():engine.play();render();});
$('step').addEventListener('click',()=>{engine.pause();onEvents(engine.step());render(true);});
$('speed-select').addEventListener('change',()=>{engine.setSpeed(Number($('speed-select').value));render();});
$('date-select').addEventListener('change',()=>{engine.setDate(Number($('date-select').value));clearEventDetails();render(true);toast('已切换交易日，开始新一轮回放。原有事件仍可在记录中查看。');});
$('basis-select').addEventListener('change',()=>{engine.setBasis($('basis-select').value);clearEventDetails();render(true);toast('已切换下跌口径。后续穿越将按新口径触发。');});
$('seek').addEventListener('input',()=>{engine.seek(Number($('seek').value));clearEventDetails();render(true);});
$('next-event').addEventListener('click',()=>{engine.pause();const events=engine.nextEvent();if(events?.length)onEvents(events);else toast('后续日期没有新的下跌穿越。可切换口径，或选择更早的日期重放。');render(true);});
$('export').addEventListener('click',()=>{const payload={schema_version:2,kind:'historical_replay_events_with_evidence',simulated:false,prices:'historical_five_minute_bars',probabilities:'end_of_day_in_sample_association',evidence_pack:evidencePack?.metadata||null,disclaimer:engine.state.events[0]?.disclaimer||'',exported_at:new Date().toISOString(),events:engine.state.events};const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='replay-events-with-evidence.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);toast('已导出事件记录、原因概率与证据来源。');});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&engine){engine.pause();render();}});
window.addEventListener('resize',()=>{if(engine&&activePage==='monitor')renderChart();});

async function initialize(){try{const [replay,pack]=await Promise.all([fetch(new URL('../data/replay.json',import.meta.url)),fetch(new URL('../data/replay_evidence.json',import.meta.url))]);if(!replay.ok)throw new Error(`历史数据请求失败 (${replay.status})`);const data=await replay.json();evidencePack=pack.ok?await pack.json():null;if(!pack.ok)toast('证据包加载失败，事件将只显示价格与成交信息。');engine=new AttributionReplay(data,evidencePack);selectedSymbol=data.symbols.includes('000011')?'000011':String(data.symbols[0]);$('date-select').innerHTML=data.dates.map(date=>`<option value="${date}">${dateText(date)}</option>`).join('');document.querySelectorAll('#page-monitor button:disabled,#page-monitor select:disabled,#page-monitor input:disabled').forEach(el=>el.disabled=false);window.attributionDemo={engine,render:()=>render(true),get selectedSymbol(){return selectedSymbol;},get selectedEvent(){return selectedEvent;},get evidencePack(){return evidencePack;}};setPage(location.hash.slice(1)||'monitor');render(true);setInterval(()=>{try{onEvents(engine.tick());render();}catch(error){engine.pause();toast(`回放暂停：${error.message}`);}},250);}catch(error){$('stock-tape').innerHTML=`<p class="empty-state">回放数据加载失败：${escapeText(error.message)}。请刷新页面重试。</p>`;toast('无法加载历史数据，请检查网络并刷新页面。');console.error(error);}}
initialize();
