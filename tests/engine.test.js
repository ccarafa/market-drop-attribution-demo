import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {AttributionReplay,observe,simulatedReasons} from '../assets/engine.js';
const bars=prices=>prices.map((close,bar)=>({date:20260727,session:0,bar,open:close,close,high:close,low:close,volume:100}));
const fixture=prices=>({symbols:['000001'],dates:[20260727],bars:{'000001':{'20260727':bars(prices)}},daily:{'000001':{'20260727':{prev_close:100}}}});
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
test('fictional reasons are reproducible, three unique entries, descending probabilities and markers',()=>{
  const r=simulatedReasons('000001',20260727,35100,'drawdown');assert.equal(r.length,3);
  assert.equal(new Set(r.map(x=>x.id)).size,3);assert.equal(r.reduce((s,x)=>s+x.probability,0),100);
  assert.ok(r.every((x,i)=>x.simulated&&x.description.startsWith('虚构情景')&&(!i||r[i-1].probability>x.probability)));
  assert.deepEqual(r,simulatedReasons('000001',20260727,35100,'drawdown'));
});
test('a simulated clock emits event immediately when the completed close becomes available',()=>{
  const e=new AttributionReplay(fixture([100,99,94]));e.seek(34800);e.setSpeed(60);e.play();
  const anchor=e.anchor;assert.equal(e.tick(anchor+4999).length,0);assert.equal(e.tick(anchor+5000).length,1);assert.equal(e.tick(anchor+5001).length,0);
});

test('Demo probabilities vary by event without round tens or replay drift',()=>{
  const data=JSON.parse(readFileSync(new URL('../data/replay.json',import.meta.url),'utf8'));
  const engine=new AttributionReplay(data),allocations=new Set();
  for(const basis of ['drawdown','day_return'])for(const date of data.dates){
    engine.setBasis(basis);engine.setDate(date);engine.seek(34200);
    for(const event of engine.move(54000)){
    const values=event.reasons.map(x=>x.probability);
    assert.ok(values.every((v,i)=>Number.isInteger(v)&&v>0&&v%10!==0&&(!i||values[i-1]>v)));
    assert.equal(values.reduce((a,b)=>a+b,0),100);
    assert.deepEqual(event.reasons,simulatedReasons(event.symbol,event.date,event.seconds,basis));
    allocations.add(values.join(','));
    }
  }
  assert.ok(allocations.size>20);
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
  assert.ok(count>0);const events=e.nextEvent();assert.ok(events.length>0);assert.ok(events.every(x=>x.simulated&&x.price_source==='historical'));
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
