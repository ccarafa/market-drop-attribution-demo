import {PAGE_SIZE,isFiniteNumber as finite,decodeRow,eventId,filterEvents,historyThroughEvent,selectInitialEvent,evidenceExport} from './study-core.js?v=20261015-1';

const $=id=>document.getElementById(id);
const escapeText=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const dateText=value=>String(value??'').replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3');
const number=(value,digits=2)=>finite(value)?value.toLocaleString('zh-CN',{minimumFractionDigits:digits,maximumFractionDigits:digits}):'—';
const percent=(value,digits=2)=>finite(value)?`${value>0?'+':''}${number(value*100,digits)}%`:'—';
const difference=value=>finite(value)?`${value>0?'+':''}${number(value*100,2)}`:'—';
const ratio=value=>finite(value)?`${number(value,2)}×`:'—';
const compact=value=>!finite(value)?'—':Math.abs(value)>=1e8?`${number(value/1e8,2)} 亿`:Math.abs(value)>=1e4?`${number(value/1e4,2)} 万`:number(value,0);
const dateTime=value=>value?String(value).replace('T',' ').replace('+08:00','（上海时间）'):'未提供';
const fallbackFlags={invalid_return:'当日收益无效',invalid_price:'价格记录无效',reference_history_missing:'缺少上一观测日，参考价连续性未核查',reference_gap_gt_1pct:'参考前收与上一观测日末价相差超过 1%',extreme_return_gt_50pct:'当日收益绝对值超过 50%',volume_history_lt_5:'成交量此前 5 日有效观察不足',spread_history_lt_5:'价差此前 5 日有效观察不足',imbalance_history_lt_5:'盘口失衡此前 5 日有效观察不足',invalid_volume:'当日成交量无效',invalid_spread:'当日价差无效',invalid_imbalance:'当日盘口失衡无效'};
let data=null,events=[],filteredEvents=[],selectedEvent=null,displayedHistory=[],page=0,selectionVersion=0,loadState='loading',seriesFile='',toastTimer=null;
const seriesCache=new Map();
const controls=['study-search','study-date','study-quality','study-sort','study-reset'];
function status(message,error=false){$('study-status').textContent=message;$('study-status').hidden=!message;$('study-status').dataset.error=String(error);$('study-status').setAttribute('role',error?'alert':'status');$('study-retry').hidden=!error;}
function notify(message){$('study-toast').textContent=message;$('study-toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('study-toast').hidden=true,4500);}
async function fetchJSON(path){const response=await fetch(new URL(`../${path}`,import.meta.url));if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json();}
function currentFilters(){return {search:$('study-search').value,date:$('study-date').value,quality:$('study-quality').value,sort:$('study-sort').value};}
function getExport(){if(loadState!=='ready'||!selectedEvent)throw Error('事件历史尚未载入');return evidenceExport(data,selectedEvent,displayedHistory,seriesFile);}

function renderList(){
  const pages=Math.ceil(filteredEvents.length/PAGE_SIZE);page=Math.max(0,Math.min(page,Math.max(0,pages-1)));
  $('study-event-count').textContent=`${number(filteredEvents.length,0)} 个事件`;
  $('study-page').textContent=pages?`${page+1} / ${number(pages,0)}`:'0 / 0';
  $('study-prev').disabled=page===0||!pages;$('study-next').disabled=page+1>=pages;
  const visible=filteredEvents.slice(page*PAGE_SIZE,(page+1)*PAGE_SIZE);
  $('study-event-list').innerHTML=visible.length?visible.map(event=>{
    const id=eventId(event),active=selectedEvent&&eventId(selectedEvent)===id;
    return `<button type="button" class="event-item" data-event-id="${escapeText(id)}" aria-pressed="${Boolean(active)}" aria-label="${escapeText(event.symbol)}，${dateText(event.date)}，跌幅 ${percent(event.return)}，${event.screened?'通过口径筛查':'口径待核查'}"><strong>${escapeText(event.symbol)}</strong><time datetime="${dateText(event.date)}">${dateText(event.date)}</time><span class="event-change">${percent(event.return)}</span><span class="event-state ${event.screened?'':'flagged'}">${event.screened?'通过筛查':'口径待核查'}</span></button>`;
  }).join(''):'<div class="ledger-empty"><strong>没有符合条件的事件</strong>可清空股票代码、切换日期，或点击“重置”查看全部事件。</div>';
}

function renderEmpty(){
  $('study-selection').innerHTML='<div><span class="section-label">所选事件</span><h2 id="selection-heading">没有匹配事件</h2><p>调整左侧条件后继续查看历史证据。</p></div>';
  $('study-quality-flags').hidden=true;$('study-evidence').innerHTML='<p class="evidence-waiting">当前筛选结果为空，没有计算或补充任何证据。</p>';
  $('study-history-caption').textContent='等待选择事件';$('study-availability').textContent='未选择事件。';$('study-export').disabled=true;
  renderChart();
}

function metric(value,unit=''){return finite(value)?`${number(value,2)}${unit?`<small>${unit}</small>`:''}`:'历史不足';}
function pairs(rows){return rows.map(([name,value])=>`<dt>${escapeText(name)}</dt><dd>${escapeText(value)}</dd>`).join('');}
function renderEvidence(){
  const e=selectedEvent;
  $('study-selection').innerHTML=`<div><span class="section-label">所选日事件</span><h2 id="selection-heading">${escapeText(e.symbol)}<small>${dateText(e.date)}</small></h2><p><span class="screen-badge ${e.screened?'':'flagged'}">${e.screened?'通过口径筛查':'口径待核查'}</span> 当日末价 ${number(e.last_price)}，参考前收 ${number(e.prev_close)}</p></div><div class="selected-quote"><span>原始当日跌幅</span><strong>${percent(e.return)}</strong><small>当日末价 / 参考前收 − 1</small></div>`;
  const flags=Array.isArray(e.flags)?e.flags:[],labels={...fallbackFlags,...data.metadata?.flag_labels};
  $('study-quality-flags').hidden=false;$('study-quality-flags').className=`quality-note ${e.screened?'':'flagged'}`;
  $('study-quality-flags').innerHTML=`<strong>${e.screened?'口径检查':'该事件保留供核查，未进入筛查后基准'}</strong>${flags.length?`<ul>${flags.map(flag=>`<li>${escapeText(labels[flag]??flag)}</li>`).join('')}</ul>`:'参考价连续性与极端收益阈值通过检查；尚未完成复权及公司行动核实。'}${!e.screened?'<p>以下保留实际记录和描述性统计，不能据此解释经济性亏损或确定原因。</p>':''}`;
  const marketValue=finite(e.excess_return)?`${difference(e.excess_return)}<small>百分点</small>`:'无法比较';
  const volumeValue=finite(e.volume_ratio)?`${number(e.volume_ratio)}<small>倍</small>`:'历史不足';
  const spreadValue=finite(e.spread_bps)?metric(e.spread_bps,'基点'):'记录缺失';
  $('study-evidence').innerHTML=`<article class="evidence-card" data-evidence="market"><span class="evidence-kind">同期样本比较</span><div class="evidence-value ${finite(e.excess_return)?'':'unavailable'}">${marketValue}</div><h4>所选股票收益 − 样本中位数</h4><dl>${pairs([['样本收益中位数',percent(e.sample_median_return)],['筛查后比较样本',`${number(e.baseline_n,0)} 只`],['样本跌逾 5% 占比',percent(e.down5_breadth)]])}</dl><p>同日样本排除所选股票；不是官方指数或行业基准，差额不是个股原因的贡献。</p></article>
  <article class="evidence-card" data-evidence="volume"><span class="evidence-kind">成交活动变化</span><div class="evidence-value ${finite(e.volume_ratio)?'':'unavailable'}">${volumeValue}</div><h4>当日成交量 / 此前 5 日中位数</h4><dl>${pairs([['当日成交量',compact(e.volume)],['此前中位数',compact(e.volume_prior5_median)],['窗口内有效观察',`${number(e.volume_prior5_count,0)} / 5 日`]])}</dl><p>沿用源成交量单位。完整窗口才展示比值，不能由量变推断买卖主体或动机。</p></article>
  <article class="evidence-card" data-evidence="liquidity"><span class="evidence-kind">盘口与价差观察</span><div class="evidence-value ${finite(e.spread_bps)?'':'unavailable'}">${spreadValue}</div><h4>当日平均相对买卖价差</h4><dl>${pairs([['较此前 5 日价差',ratio(e.spread_ratio)],['价差有效观察',`${number(e.spread_prior5_count,0)} / 5 日`],['绝对失衡变化',finite(e.imbalance_change)?`${difference(e.imbalance_change)} 百分点`:'历史不足'],['失衡有效观察',`${number(e.imbalance_prior5_count,0)} / 5 日`]])}</dl><p>价差与失衡为日汇总统计，不识别真实交易动机或因果关系。</p></article>`;
  $('study-availability').textContent=`可用时间：${dateTime(e.available_at)}。最后快照：${dateTime(e.last_snapshot_at)}。事件标识 ${eventId(e)}。`;
}

function renderChart(){
  const svg=$('study-chart'),rows=displayedHistory,e=selectedEvent;
  svg.dataset.dates=JSON.stringify(rows.map(row=>row.date));
  const W=Math.max(280,Math.round($('study-chart-wrap').getBoundingClientRect().width)),H=W<450?280:310,L=43,R=18,T=21,priceBottom=H-130,returnTop=H-100,returnBottom=H-36;
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  if(!rows.length){svg.innerHTML='';svg.setAttribute('aria-label',e?'股票历史正在载入或不可用':'没有匹配事件的价格路径');$('study-chart-message').hidden=false;$('study-chart-message').textContent=e?'正在载入该股票历史记录…':'选择事件后显示历史路径';return;}
  $('study-chart-message').hidden=true;
  const prices=rows.map(row=>row.last_price).filter(value=>finite(value)&&value>0),returns=rows.map(row=>row.return).filter(finite);
  if(!prices.length){svg.innerHTML='';$('study-chart-message').hidden=false;$('study-chart-message').textContent='这些日期没有可绘制的有效价格。';return;}
  let low=Math.min(...prices),high=Math.max(...prices);const padding=Math.max((high-low)*.18,high*.006,.02);low-=padding;high+=padding;
  const x=i=>L+(rows.length===1?.5:i/(rows.length-1))*(W-L-R),y=value=>T+(high-value)/(high-low)*(priceBottom-T);
  const rMin=Math.min(...returns,-.05)*1.12,rMax=Math.max(...returns,.015)*1.12,ry=value=>returnTop+(rMax-value)/(rMax-rMin)*(returnBottom-returnTop),zero=ry(0);
  const parts=[`<title>${escapeText(e.symbol)} 截至 ${dateText(e.date)} 的 ${rows.length} 个股票日观察</title><desc>上方为未复权末价，下方为相对参考前收的当日涨跌幅。仅含事件日及此前数据。</desc>`];
  for(let i=0;i<4;i++){const value=high-(high-low)*i/3,yp=y(value);parts.push(`<line x1="${L}" x2="${W-R}" y1="${yp}" y2="${yp}" stroke="#e8edf4"/><text x="${L-8}" y="${yp+4}" text-anchor="end" fill="#8491a3" font-size="10">${number(value,2)}</text>`);}
  parts.push(`<text x="${L}" y="${returnTop-9}" fill="#7c8ba0" font-size="10">当日涨跌幅</text><line x1="${L}" x2="${W-R}" y1="${zero}" y2="${zero}" stroke="#dce3ed"/><text x="${L-8}" y="${zero+3}" text-anchor="end" fill="#8491a3" font-size="9">0%</text>`);
  if(ry(-.05)>=returnTop&&ry(-.05)<=returnBottom)parts.push(`<line x1="${L}" x2="${W-R}" y1="${ry(-.05)}" y2="${ry(-.05)}" stroke="#c7d1e1" stroke-dasharray="3 4"/><text x="${L-8}" y="${ry(-.05)+3}" text-anchor="end" fill="#8491a3" font-size="9">−5%</text>`);
  const currentX=x(rows.length-1);parts.push(`<line x1="${currentX}" x2="${currentX}" y1="${T}" y2="${returnBottom}" stroke="#a9b5dc" stroke-dasharray="3 4"/>`);
  let path='',inSegment=false;
  rows.forEach((row,index)=>{if(finite(row.last_price)&&row.last_price>0){path+=`${inSegment?'L':'M'}${x(index)},${y(row.last_price)} `;inSegment=true;}else inSegment=false;});
  parts.push(`<path d="${path}" fill="none" stroke="#4354b9" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`);
  const barWidth=Math.max(3,Math.min(15,(W-L-R)/Math.max(rows.length,12)*.58));
  rows.forEach((row,index)=>{
    const xp=x(index),selected=Number(row.date)===Number(e.date),title=`${dateText(row.date)}，末价 ${number(row.last_price)}，当日涨跌幅 ${percent(row.return)}${row.screened?'':'，口径待核查'}`;
    if(finite(row.return)){const yp=ry(row.return);parts.push(`<rect x="${xp-barWidth/2}" y="${Math.min(yp,zero)}" width="${barWidth}" height="${Math.max(1,Math.abs(yp-zero))}" fill="${row.return<0?'#6ba497':'#c57c85'}" opacity="${selected?1:.62}"><title>${escapeText(title)}</title></rect>`);}
    if(finite(row.last_price)&&row.last_price>0)parts.push(`<circle cx="${xp}" cy="${y(row.last_price)}" r="${selected?4:2.6}" fill="${selected?'#4354b9':'white'}" stroke="${row.screened?'#6f7dc2':'#b78b47'}" stroke-width="1.5"/><circle cx="${xp}" cy="${y(row.last_price)}" r="7" fill="transparent" tabindex="0" role="img" aria-label="${escapeText(title)}"><title>${escapeText(title)}</title></circle>`);
  });
  const marks=new Set([0,Math.floor((rows.length-1)/2),rows.length-1]);if(W>620){marks.add(Math.floor((rows.length-1)/4));marks.add(Math.floor((rows.length-1)*3/4));}
  for(const index of [...marks].sort((a,b)=>a-b)){parts.push(`<text x="${x(index)}" y="${H-12}" text-anchor="middle" fill="#8290a3" font-size="10">${dateText(rows[index].date).slice(5).replace('-','/')}</text>`);}
  svg.innerHTML=parts.join('');svg.setAttribute('aria-label',`${e.symbol}，${dateText(rows[0].date)} 至 ${dateText(e.date)}，${rows.length} 个已观察交易日。未复权价格与当日涨跌幅，未显示事件后的价格。`);
  $('study-history-caption').textContent=`${dateText(rows[0].date)} 至 ${dateText(e.date)}，${rows.length} 个已观察交易日；日汇总末价`;
}

async function selectEvent(id){
  const event=events.find(row=>eventId(row)===String(id));if(!event)return;
  const version=++selectionVersion;selectedEvent=event;displayedHistory=[];loadState='loading';$('study-export').disabled=true;
  renderList();renderEvidence();renderChart();status('');
  const prefix=String(event.symbol).slice(0,data.series?.prefix_length??1);
  seriesFile=(data.series?.path_template??'data/series/{prefix}.json').replace('{prefix}',prefix);
  try{
    if(!seriesCache.has(seriesFile))seriesCache.set(seriesFile,fetchJSON(seriesFile).catch(error=>{seriesCache.delete(seriesFile);throw error;}));
    const shard=await seriesCache.get(seriesFile);if(version!==selectionVersion)return;
    if(!Array.isArray(shard.columns)||!Array.isArray(shard.symbols?.[event.symbol]))throw Error('历史文件缺少股票记录');
    const history=shard.symbols[event.symbol].map(row=>({...decodeRow(shard.columns,row),symbol:event.symbol}));
    displayedHistory=historyThroughEvent(history,event);if(!displayedHistory.some(row=>row.date===event.date))throw Error('历史文件缺少事件日');
    loadState='ready';renderChart();$('study-export').disabled=false;
  }catch(error){if(version!==selectionVersion)return;loadState='error';$('study-chart-message').textContent='该股票历史未能载入；可重试或选择其他事件。';status(`股票历史加载失败（${error.message}）。请选择“重新加载”重试。`,true);}
}

function updateFilters(){
  if(!data)return;page=0;filteredEvents=filterEvents(events,currentFilters());
  if(!filteredEvents.length){selectionVersion++;selectedEvent=null;displayedHistory=[];loadState='empty';status('');renderList();renderEmpty();return;}
  selectEvent(eventId(filteredEvents[0]));
}
function resetFilters(){for(const id of ['study-search','study-date'])$(id).value='';$('study-quality').value='all';$('study-sort').value='date_desc';updateFilters();}
async function initialize(){
  loadState='loading';status('正在载入历史事件与数据说明…');$('study-retry').hidden=true;controls.forEach(id=>$(id).disabled=true);
  try{
    const result=await fetchJSON('data/study.json');
    if(result.schema_version!==1||!Array.isArray(result.columns)||!Array.isArray(result.events)||!result.summary)throw Error('数据格式无效');
    data=result;events=data.events.map(row=>decodeRow(data.columns,row));
    filteredEvents=filterEvents(events,currentFilters());const initial=selectInitialEvent(filteredEvents,data.metadata?.default_event_id);page=initial?Math.floor(filteredEvents.indexOf(initial)/PAGE_SIZE):0;
    const summary=data.summary;for(const [id,key] of [['study-stock-total','stocks'],['study-date-total','dates'],['study-row-total','stock_days'],['study-event-total','raw_events'],['study-screened-total','screened_events']])$(id).textContent=number(summary[key],0);
    $('study-date').innerHTML='<option value="">全部日期</option>'+[...data.dates].reverse().map(date=>`<option value="${date}">${dateText(date)}</option>`).join('');
    $('study-provenance').textContent=`本版覆盖 ${number(summary.stocks,0)} 只股票、${number(summary.stock_days,0)} 个股票日。共 ${number(summary.raw_events,0)} 个原始日事件，其中 ${number(summary.screened_events,0)} 个通过预设口径筛查。全部原始事件仍可查询。`;
    window.attributionStudy={get data(){return data;},get selectedEvent(){return selectedEvent;},get filteredEvents(){return filteredEvents;},get history(){return displayedHistory;},get page(){return page;},get loadState(){return loadState;},getExport,selectEvent};
    controls.forEach(id=>$(id).disabled=false);status('');
    if(initial)await selectEvent(eventId(initial));else{loadState='empty';renderList();renderEmpty();}
  }catch(error){loadState='error';status(`历史数据加载失败（${error.message}）。请检查连接后重新加载。`,true);}
}

$('study-search').addEventListener('input',updateFilters);
for(const id of ['study-date','study-quality','study-sort'])$(id).addEventListener('change',updateFilters);
$('study-reset').addEventListener('click',resetFilters);
$('study-retry').addEventListener('click',()=>data&&selectedEvent?selectEvent(eventId(selectedEvent)):initialize());
$('study-prev').addEventListener('click',()=>{if(page>0){page--;selectEvent(eventId(filteredEvents[page*PAGE_SIZE]));$('study-event-list').scrollTop=0;}});
$('study-next').addEventListener('click',()=>{if((page+1)*PAGE_SIZE<filteredEvents.length){page++;selectEvent(eventId(filteredEvents[page*PAGE_SIZE]));$('study-event-list').scrollTop=0;}});
$('study-event-list').addEventListener('click',event=>{const button=event.target.closest('[data-event-id]');if(button)selectEvent(button.dataset.eventId);});
$('study-event-list').addEventListener('keydown',event=>{
  if(!['ArrowUp','ArrowDown','Home','End'].includes(event.key))return;const target=event.target.closest('[data-event-id]');if(!target)return;event.preventDefault();
  const current=filteredEvents.findIndex(row=>eventId(row)===target.dataset.eventId);
  const index=event.key==='Home'?0:event.key==='End'?filteredEvents.length-1:Math.max(0,Math.min(filteredEvents.length-1,current+(event.key==='ArrowDown'?1:-1)));
  page=Math.floor(index/PAGE_SIZE);selectEvent(eventId(filteredEvents[index]));
  const button=$('study-event-list').querySelector(`[data-event-id="${eventId(filteredEvents[index])}"]`);button?.focus({preventScroll:true});button?.scrollIntoView({block:'nearest'});
});
$('study-export').addEventListener('click',()=>{
  try{const payload={...getExport(),exported_at:new Date().toISOString()},url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`event-evidence-${eventId(selectedEvent)}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),5000);notify('已导出本事件、截至事件日的历史与来源说明。');}catch(error){notify(error.message);}
});
let resizeFrame;window.addEventListener('resize',()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(renderChart);});
initialize();
