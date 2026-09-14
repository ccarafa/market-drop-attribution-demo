// Historical threshold detection with explicitly fictional explanation scenarios.
export const DISCLAIMER='虚构归因演示：原因和概率均为人为编写，未接入真实新闻、行业或财报，不能作为事实或因果结论。';
export const THRESHOLD=-0.05;
const TOLERANCE=1e-12;
export const closeSeconds=b=>(b.session===0?34200:46800)+(b.bar+1)*300;
export const timeText=s=>[Math.floor(s/3600),Math.floor(s%3600/60),Math.floor(s%60)].map(x=>String(x).padStart(2,'0')).join(':');
const below=x=>Number.isFinite(x)&&x<THRESHOLD-TOLERANCE;
const finitePositive=x=>typeof x==='number'&&Number.isFinite(x)&&x>0;
const REASONS=[
  {id:'earnings',category:'公司公告',title:'财报表现低于市场预期',description:'虚构情景：假设事件前刚发布的财报显示利润低于预期，市场下调估值。没有核验该股票的真实公告或财务表现。'},
  {id:'regulation',category:'政策监管',title:'行业监管政策趋严',description:'虚构情景：假设相关监管部门刚提出更严格的经营要求，投资者担忧未来盈利。未接入实际政策或股票行业归属。'},
  {id:'spillover',category:'行业传导',title:'同行下跌引发情绪传导',description:'虚构情景：假设某家同行先出现负面消息，卖压随后扩散。未核验同行名单、消息时间或传导关系。'},
  {id:'guidance',category:'经营公告',title:'经营展望被下调',description:'虚构情景：假设公司刚在经营公告中下调后续收入展望，投资者调整预期。此处没有对应的真实公告。'},
  {id:'demand',category:'商业消息',title:'需求与订单预期转弱',description:'虚构情景：假设出现需求放缓或订单预期变化的商业消息，引发短期重新定价。没有核验任何具体客户或订单。'},
  {id:'liquidity',category:'交易压力',title:'集中卖出与流动性压力',description:'虚构情景：假设短时间集中卖出扩大价格冲击。当前展示并未识别真实资金身份或验证卖出动机。'},
  {id:'macro',category:'宏观消息',title:'风险偏好短时回落',description:'虚构情景：假设宏观消息影响市场风险偏好，带动相关资产卖出。没有接入对应新闻或市场因果证据。'}
];
const PROFILES=[[0,1,2],[1,2,5],[3,4,6],[4,0,5],[5,6,2],[2,1,3]];
// Demo-only integer allocations: descending, sum to 100, no round tens.
const WEIGHT_OPTIONS=[];
for(let a=46;a<=67;a++)for(let b=19;b<=37;b++){
  const c=100-a-b;
  if(a>b&&b>c&&c>=7&&[a,b,c].every(x=>x%10!==0))WEIGHT_OPTIONS.push([a,b,c]);
}
export function simulatedReasons(symbol,date,seconds,basis){
  // Stable choice of a hand-written scenario is for repeatable UI demonstrations only.
  let h=0;for(const ch of `${symbol}|${date}|${seconds}|${basis}`)h=(Math.imul(h,31)+ch.charCodeAt(0))>>>0;
  const profile=PROFILES[h%PROFILES.length];
  // Stable per-event mixing keeps replay, popup, history and export consistent.
  let seed=Math.imul(h^(h>>>16),0x45d9f3b)>>>0;
  seed=(seed^(seed>>>16))>>>0;
  const weights=WEIGHT_OPTIONS[seed%WEIGHT_OPTIONS.length];
  return profile.map((i,k)=>({...REASONS[i],probability:weights[k],simulated:true,probability_label:'Demo probability'}));
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
  constructor(data){
    if(!data||!Array.isArray(data.symbols)||!data.symbols.length||!Array.isArray(data.dates)||!data.dates.length)throw Error('历史数据包无效');
    this.data=data;this.sequence=0;this.counter=0;this.catalog=new Map();this.emitted=new Set();
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
  move(target){
    if(!Number.isFinite(target)||target<this.state.cursor||target>54000)throw Error('回放时间无效');
    const fresh=[];
    for(const e of this.eventCatalog(this.state.date)){
      const key=`${e.date}|${e.symbol}|${e.seconds}|${e.basis}`;
      if(e.seconds<=this.state.cursor||e.seconds>target||this.emitted.has(key))continue;
      this.emitted.add(key);
      const event={...e,id:++this.sequence,runId:this.state.runId,time:timeText(e.seconds),reasons:simulatedReasons(e.symbol,e.date,e.seconds,e.basis),simulated:true,price_source:'historical',explanation_source:'handwritten_demo',disclaimer:DISCLAIMER};
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
