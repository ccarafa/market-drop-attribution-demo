// Historical threshold detection with real, time-stamped evidence for every event.
export const DISCLAIMER='原因与概率来自日终归因（2026 年 7 月样本内经验关联），证据按事件时点区分“事件前已知”与“事后披露”；不是因果证明。';
export const THRESHOLD=-0.05;
const TOLERANCE=1e-12;
export const closeSeconds=b=>(b.session===0?34200:46800)+(b.bar+1)*300;
export const timeText=s=>[Math.floor(s/3600),Math.floor(s%3600/60),Math.floor(s%60)].map(x=>String(x).padStart(2,'0')).join(':');
const below=x=>Number.isFinite(x)&&x<THRESHOLD-TOLERANCE;
const finitePositive=x=>typeof x==='number'&&Number.isFinite(x)&&x>0;
export const isoAt=(date,seconds)=>{const d=String(date);return `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}T${timeText(seconds).slice(0,5)}+08:00`;};
const median=values=>{const v=[...values].sort((a,b)=>a-b);return v.length?(v.length%2?v[(v.length-1)/2]:(v[v.length/2-1]+v[v.length/2])/2):null;};

// Split a stock-day's evidence into what was published at or before the crossing
// bar's close and what was disclosed afterwards. Times are Asia/Shanghai ISO strings
// of equal format, so lexical comparison is chronological.
export function eventEvidence(pack,symbol,date,seconds){
  const day=pack?.days?.[String(symbol)]?.[String(date)];
  const cutoff=isoAt(date,seconds);
  const empty={announcements:[],news_company:[],news_industry:[],macro:[]};
  if(!day)return {available:false,cutoff,reasons:[],attribution:null,known:empty,later:{...empty},flow:null,daily_return:null,market_return:null,name:pack?.symbols?.[symbol]?.name??null,industry:pack?.symbols?.[symbol]?.industry??null};
  const split=items=>{const known=[],later=[];for(const item of items||[])(String(item.time)<=cutoff?known:later).push(item);return [known,later];};
  const [annoKnown,annoLater]=split(day.announcements),[companyKnown,companyLater]=split(day.news_company),[industryKnown,industryLater]=split(day.news_industry),[macroKnown,macroLater]=split(pack.macro_by_date?.[String(date)]);
  const attribution=day.attribution||null;
  const reasons=attribution?attribution.causes.map(c=>({id:c.f,title:c.label,probability:Math.round(c.p*1000)/10,likelihood_ratio:c.lr??null,event_rate:c.er??null,support:c.n??null,signals:c.signals||[],flow:c.f==='flow'})):[];
  return {available:true,cutoff,reasons,attribution,known:{announcements:annoKnown,news_company:companyKnown,news_industry:industryKnown,macro:macroKnown},later:{announcements:annoLater,news_company:companyLater,news_industry:industryLater,macro:macroLater},flow:day.flow||null,moneyflow:day.moneyflow||null,daily_return:day.daily_return??null,market_return:day.market_return??null,down5_breadth:day.down5_breadth??null,screened:day.screened??null,name:pack.symbols?.[symbol]?.name??null,industry:pack.symbols?.[symbol]?.industry??null};
}

// Trading-activity facts from the five-minute bars completed by the crossing bar.
export function barFlow(bars,seconds){
  const done=bars.filter(b=>closeSeconds(b)<=seconds);
  const index=done.findIndex(b=>closeSeconds(b)===seconds);
  if(index<0)return null;
  const bar=done[index],prior=done.slice(0,index);
  const priorVolume=prior.map(b=>b.volume).filter(v=>Number.isFinite(v)&&v>0);
  const center=priorVolume.length>=3?median(priorVolume):null;
  const peak=prior.reduce((m,b)=>finitePositive(b.close)?Math.max(m??b.close,b.close):m,null);
  let streak=0;for(let i=index;i>=0&&finitePositive(done[i].close)&&finitePositive(done[i].open)&&done[i].close<done[i].open;i--)streak++;
  const sessionVolume=done.reduce((s,b)=>s+(Number.isFinite(b.volume)?b.volume:0),0);
  return {bar_volume:bar.volume,prior_median_volume:center,volume_multiple:center?bar.volume/center:null,bar_return:finitePositive(bar.open)?bar.close/bar.open-1:null,drop_from_peak:finitePositive(peak)?bar.close/peak-1:null,down_bar_streak:streak,bars_completed:done.length,session_volume:sessionVolume,bar_share_of_session:sessionVolume>0?bar.volume/sessionVolume:null};
}

export function observe(bars,previousClose,basis){
  if(!['drawdown','day_return'].includes(basis))throw Error('跌幅口径无效');
  let peak=null,under=false,latest={price:null,reference:null,change:null,available:false};const crossings=[];
  for(const bar of bars){
    if(!finitePositive(bar.close))continue;
    const reference=basis==='day_return'?previousClose:peak;
    const available=finitePositive(reference),change=available?bar.close/reference-1:null;
    const isUnder=below(change);
    if(isUnder&&!under)crossings.push({seconds:closeSeconds(bar),price:bar.close,reference,change});
    under=isUnder;peak=peak===null?bar.close:Math.max(peak,bar.close);
    latest={price:bar.close,reference:available?reference:null,change,available};
  }
  return {crossings,latest};
}

export class AttributionReplay{
  constructor(data,evidence=null){
    if(!data||!Array.isArray(data.symbols)||!data.symbols.length||!Array.isArray(data.dates)||!data.dates.length)throw Error('历史数据包无效');
    this.data=data;this.evidence=evidence;this.sequence=0;this.counter=0;this.catalog=new Map();this.emitted=new Set();
    this.state={date:data.dates.includes(20260727)?20260727:data.dates[0],cursor:48900,running:false,speed:60,basis:'drawdown',events:[],currentEvents:[],latest:{},runId:1};
    this.anchor=performance.now();this.refreshLatest();
  }
  bars(symbol,date=this.state.date){return this.data.bars[symbol]?.[String(date)]||[];}
  previousClose(symbol,date=this.state.date){return this.data.daily[symbol]?.[String(date)]?.prev_close;}
  visibleBars(symbol){return this.bars(symbol).filter(b=>closeSeconds(b)<=this.state.cursor).map(b=>({...b,time:timeText(closeSeconds(b))}));}
  priceState(symbol){return observe(this.visibleBars(symbol),this.previousClose(symbol),this.state.basis).latest;}
  refreshLatest(){this.state.latest=Object.fromEntries(this.data.symbols.map(s=>[s,this.priceState(s)]));}
  eventCatalog(date,basis=this.state.basis){
    const key=`${date}|${basis}`;
    if(!this.catalog.has(key)){
      const found=[];
      for(const symbol of this.data.symbols){for(const item of observe(this.bars(symbol,date),this.previousClose(symbol,date),basis).crossings)found.push({...item,symbol,date,basis});}
      found.sort((a,b)=>a.seconds-b.seconds||a.symbol.localeCompare(b.symbol));this.catalog.set(key,found);
    }
    return this.catalog.get(key);
  }
  describe(e){
    const evidence=eventEvidence(this.evidence,e.symbol,e.date,e.seconds);
    return {...e,time:timeText(e.seconds),name:evidence.name,industry:evidence.industry,reasons:evidence.reasons,evidence,intraday:barFlow(this.bars(e.symbol,e.date),e.seconds),simulated:false,price_source:'historical',explanation_source:evidence.available?'end_of_day_attribution':'evidence_unavailable',disclaimer:DISCLAIMER};
  }
  move(target){
    if(!Number.isFinite(target)||target<this.state.cursor||target>54000)throw Error('回放时间无效');
    const fresh=[];
    for(const e of this.eventCatalog(this.state.date)){
      const key=`${e.date}|${e.symbol}|${e.seconds}|${e.basis}`;
      if(e.seconds<=this.state.cursor||e.seconds>target||this.emitted.has(key))continue;
      this.emitted.add(key);
      const event={...this.describe(e),id:++this.sequence,runId:this.state.runId};
      this.state.events.push(event);this.state.currentEvents.push(event);fresh.push(event);
    }
    this.state.cursor=target;if(target===54000)this.state.running=false;this.refreshLatest();return fresh;
  }
  tick(now=performance.now()){
    const elapsed=Math.max(0,now-this.anchor);this.anchor=now;
    return this.state.running?this.move(Math.min(54000,this.state.cursor+elapsed/1000*this.state.speed)):[];
  }
  step(){this.pause();return this.move(Math.min(54000,(Math.floor(this.state.cursor/300)+1)*300));}
  play(){this.anchor=performance.now();if(this.state.cursor<54000)this.state.running=true;}
  pause(){this.state.running=false;this.anchor=performance.now();}
  setSpeed(speed){if(![1,30,60,300,900].includes(speed))throw Error('回放速度无效');this.state.speed=speed;this.anchor=performance.now();}
  reset(){this.pause();this.state.runId++;this.state.currentEvents=[];this.emitted.clear();this.refreshLatest();}
  seek(seconds){if(!Number.isFinite(seconds)||seconds<34200||seconds>54000)throw Error('请选择 09:30 至 15:00');this.state.cursor=seconds;this.reset();return [];}
  setDate(date){if(!this.data.dates.includes(date))throw Error('该日期没有回放数据');this.state.date=date;this.state.cursor=34200;this.reset();}
  setBasis(basis){if(!['drawdown','day_return'].includes(basis))throw Error('跌幅口径无效');this.state.basis=basis;this.reset();}
  nextEvent(){
    const all=this.data.dates.flatMap(d=>this.eventCatalog(d));
    const event=all.find(e=>e.date>this.state.date||e.date===this.state.date&&e.seconds>this.state.cursor)||all[0];
    if(!event)return [];
    this.state.date=event.date;this.seek(Math.max(34200,event.seconds-300));return this.move(event.seconds);
  }
}
