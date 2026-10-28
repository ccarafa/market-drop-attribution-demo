import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {AttributionReplay,observe,eventEvidence,barFlow,isoAt} from '../assets/engine.js';
const bars=prices=>prices.map((close,bar)=>({date:20260727,session:0,bar,open:close,close,high:close,low:close,volume:100}));
const fixture=prices=>({symbols:['000001'],dates:[20260727],bars:{'000001':{'20260727':bars(prices)}},daily:{'000001':{'20260727':{prev_close:100}}}});
const pack=()=>({schema_version:1,metadata:{windows:{pre_open:'前',intraday:'中',post_close:'后',prior:'此前'}},symbols:{'000001':{name:'样本',industry:'银行'}},
  macro_by_date:{'20260727':[{id:'m1',title:'宏观',time:'2026-07-27T09:00+08:00'},{id:'m2',title:'午后',time:'2026-07-27T14:00+08:00'}]},
  days:{'000001':{'20260727':{daily_return:-.08,market_return:-.01,flow:{volume_ratio:2.5},
    attribution:{r:-.08,t:.05,shares:[.1,.2,.7],causes:[{f:'anno:share_reduction:neg:pre_open',label:'减持',p:.5,lr:3,er:.3,n:40},{f:'flow',label:'盘口',p:.2,signals:[{f:'volume_ratio_ge2',label:'放量'}]}]},
    announcements:[{id:1,title:'减持公告',label:'股东减持',direction:-1,time:'2026-07-24T18:00+08:00',window:'prior'},{id:2,title:'异动公告',label:'交易异常波动公告',direction:0,time:'2026-07-27T17:30+08:00',window:'post_close'}],
    news_company:[{id:'n1',title:'盘中报道',url:'https://example.test/n1',time:'2026-07-27T09:40+08:00',window:'intraday'}],news_industry:[]}}}});
test('strict 5% boundary, deduplication, recovery and a second drop',()=>{
  const {crossings}=observe(bars([100,95,94.9,94,96,94]),100,'drawdown');
  assert.deepEqual(crossings.map(e=>e.seconds),[35100,36000]);
  assert.deepEqual(observe(bars([94,93,95,94]),100,'day_return').crossings.map(e=>e.seconds),[34500,35400]);
});
test('reference uses only previously completed closes, never future peaks or intrabar highs',()=>{
  const b=bars([100,99,98,200]);b[0].high=999;
  assert.equal(observe(b.slice(0,3),100,'drawdown').crossings.length,0);
  assert.equal(observe(b.slice(0,3),100,'drawdown').latest.reference,100);
  assert.equal(observe(b,100,'drawdown').crossings.length,0);
});
test('missing previous close suppresses day-return events and first drawdown close has no reference',()=>{
  assert.equal(observe(bars([100,80]),null,'day_return').crossings.length,0);
  assert.equal(observe(bars([100]),100,'drawdown').latest.available,false);
});
test('step emits only at observation time and seek never backfills popups',()=>{
  const e=new AttributionReplay(fixture([100,99,94]));e.seek(34200);
  assert.equal(e.step().length,0);assert.equal(e.step().length,0);
  assert.equal(e.visibleBars('000001').length,2);
  assert.equal(e.step().length,1);assert.equal(e.state.currentEvents.length,1);
  assert.equal(e.tick().length,0);e.seek(35100);assert.equal(e.state.currentEvents.length,0);assert.equal(e.state.events.length,1);
});
test('evidence is split by the crossing time and reasons come from the end-of-day attribution',()=>{
  assert.equal(isoAt(20260727,35100),'2026-07-27T09:45+08:00');
  const ev=eventEvidence(pack(),'000001',20260727,35100);
  assert.equal(ev.available,true);assert.equal(ev.name,'样本');
  assert.deepEqual(ev.known.announcements.map(a=>a.id),[1]);assert.deepEqual(ev.later.announcements.map(a=>a.id),[2]);
  assert.deepEqual(ev.known.news_company.map(a=>a.id),['n1']);assert.deepEqual(ev.known.macro.map(a=>a.id),['m1']);assert.deepEqual(ev.later.macro.map(a=>a.id),['m2']);
  assert.deepEqual(ev.reasons.map(r=>[r.title,r.probability,r.flow]),[['减持',50,false],['盘口',20,true]]);
  assert.equal(eventEvidence(pack(),'000001',20260727,34500).known.news_company.length,0);
  const none=eventEvidence(null,'000001',20260727,35100);assert.equal(none.available,false);assert.deepEqual(none.reasons,[]);
});
test('bar flow facts come only from completed bars up to the crossing bar',()=>{
  const b=bars([100,100,100,100,94]);b[4].volume=500;b[4].open=100;
  const flow=barFlow(b,35700);
  assert.equal(flow.bars_completed,5);assert.equal(flow.prior_median_volume,100);assert.equal(flow.volume_multiple,5);
  assert.ok(Math.abs(flow.drop_from_peak-(-.06))<1e-12);assert.equal(flow.down_bar_streak,1);assert.equal(barFlow(b,36000),null);
});
test('events carry real evidence markers instead of fictional reasons',()=>{
  const e=new AttributionReplay(fixture([100,99,94]),pack());e.seek(34200);e.step();e.step();
  const [event]=e.step();
  assert.equal(event.simulated,false);assert.equal(event.price_source,'historical');assert.equal(event.explanation_source,'end_of_day_attribution');
  assert.equal(event.reasons[0].title,'减持');assert.equal(event.evidence.cutoff,'2026-07-27T09:45+08:00');assert.ok(event.intraday.bars_completed===3);
  const bare=new AttributionReplay(fixture([100,99,94]));bare.seek(34200);bare.step();bare.step();
  assert.equal(bare.step()[0].explanation_source,'evidence_unavailable');
});
test('a simulated clock emits event immediately when the completed close becomes available',()=>{
  const e=new AttributionReplay(fixture([100,99,94]));e.seek(34800);e.setSpeed(60);e.play();
  const anchor=e.anchor;assert.equal(e.tick(anchor+4999).length,0);assert.equal(e.tick(anchor+5000).length,1);assert.equal(e.tick(anchor+5001).length,0);
});
test('real evidence pack covers every replay event with time-consistent items and conserved probabilities',()=>{
  const data=JSON.parse(readFileSync(new URL('../data/replay.json',import.meta.url),'utf8'));
  const evidence=JSON.parse(readFileSync(new URL('../data/replay_evidence.json',import.meta.url),'utf8'));
  const engine=new AttributionReplay(data,evidence);let withAttribution=0,total=0;
  for(const basis of ['drawdown','day_return'])for(const date of data.dates){
    engine.setBasis(basis);engine.setDate(date);engine.seek(34200);
    for(const event of engine.move(54000)){
      total++;const ev=event.evidence;assert.equal(ev.available,true);assert.equal(event.simulated,false);
      for(const group of ['announcements','news_company','news_industry','macro']){
        assert.ok(ev.known[group].every(item=>String(item.time)<=ev.cutoff));assert.ok(ev.later[group].every(item=>String(item.time)>ev.cutoff));
      }
      assert.ok(event.intraday&&event.intraday.bars_completed>0);
      if(ev.attribution){withAttribution++;const sum=event.reasons.reduce((s,r)=>s+r.probability,0);assert.ok(Math.abs(sum-(1-ev.attribution.shares[0]-ev.attribution.shares[1])*100)<.3,sum);assert.ok(event.reasons.some(r=>r.flow));}
      else assert.deepEqual(event.reasons,[]);
    }
  }
  assert.ok(total>0&&withAttribution>0);
});
test('real eight-stock dataset stays unchanged and catalog matches independent chronological detection',()=>{
  const raw=readFileSync(new URL('../data/replay.json',import.meta.url));
  const manifest=JSON.parse(readFileSync(new URL('../data/manifest.json',import.meta.url),'utf8'));
  assert.equal(createHash('sha256').update(raw).digest('hex'),manifest.sha256);
  const d=JSON.parse(raw);
  assert.equal(d.symbols.length,8);assert.equal(d.dates.length,23);
  const e=new AttributionReplay(d);let count=0;
  for(const basis of ['drawdown','day_return'])for(const date of d.dates){
    const expected=[];
    for(const symbol of d.symbols){let max=null,last=false;
      for(const b of d.bars[symbol][date]){const ref=basis==='drawdown'?max:d.daily[symbol][date].prev_close;
        const hit=ref>0&&b.close/ref-1<-.05-1e-12;
        if(hit&&!last)expected.push(symbol+'|'+((b.session===0?34200:46800)+(b.bar+1)*300));
        last=hit;max=Math.max(max??b.close,b.close);
      }
    }
    const actual=e.eventCatalog(date,basis).map(x=>x.symbol+'|'+x.seconds);assert.deepEqual(actual.sort(),expected.sort());count+=actual.length;
  }
  assert.ok(count>0);const events=e.nextEvent();assert.ok(events.length>0);assert.ok(events.every(x=>!x.simulated&&x.price_source==='historical'));
});
test('case navigation handles simultaneous stocks, later dates and wrap without losing events',()=>{
  const d=JSON.parse(readFileSync(new URL('../data/replay.json',import.meta.url),'utf8'));
  const e=new AttributionReplay(d);
  for(const basis of ['drawdown','day_return']){
    e.setBasis(basis);e.setDate(d.dates[0]);
    const all=d.dates.flatMap(date=>e.eventCatalog(date));
    const times=[...new Set(all.map(x=>x.date+'|'+x.seconds))];let emitted=0;
    for(const key of times){const found=e.nextEvent();assert.equal(found[0].date+'|'+found[0].seconds,key);assert.equal(found.length,all.filter(x=>x.date+'|'+x.seconds===key).length);emitted+=found.length;}
    assert.equal(emitted,all.length);assert.equal(e.nextEvent()[0].date+'|'+e.state.cursor,times[0]);
  }
});
