import test from 'node:test';
import assert from 'node:assert/strict';
import {filterEvents,historyThroughEvent,selectInitialEvent,evidenceExport} from '../assets/study-core.js';

test('stock/date/quality filters intersect and missing ratios sort last without changing source',()=>{
  const rows=[{symbol:'000001',date:20260702,return:-.06,screened:true,volume_ratio:null},{symbol:'000001',date:20260703,return:-.08,screened:false,volume_ratio:3},{symbol:'600001',date:20260703,return:-.07,screened:true,volume_ratio:2}];
  assert.deepEqual(filterEvents(rows,{search:'000001',date:'20260703',quality:'flagged'}),[rows[1]]);
  assert.deepEqual(filterEvents(rows,{sort:'volume'}),[rows[1],rows[2],rows[0]]);
  assert.deepEqual(filterEvents(rows,{sort:'decline'}),[rows[1],rows[2],rows[0]]);
  assert.deepEqual(filterEvents(rows,{search:'999999'}),[]);
  assert.equal(rows[0].date,20260702);
});

test('displayed and exported history excludes future dates and other stocks',()=>{
  const event={symbol:'000001',date:20260702,return:-.07,screened:true,volume_ratio:null,flags:['volume_history_lt_5']};
  const history=[{symbol:'000001',date:20260703,last_price:3},{symbol:'600001',date:20260701,last_price:6},{symbol:'000001',date:20260702,last_price:4},{symbol:'000001',date:20260701,last_price:5}];
  assert.deepEqual(historyThroughEvent(history,event).map(row=>row.date),[20260701,20260702]);
  const payload=evidenceExport({metadata:{default_event_id:'000001_20260702'},summary:{stocks:2}},event,history,'data/series/0.json');
  assert.equal(payload.simulated,false);assert.deepEqual(payload.event,event);assert.equal(payload.event.volume_ratio,null);
  assert.deepEqual(payload.history.map(row=>row.date),[20260701,20260702]);assert.equal(payload.provenance.series_file,'data/series/0.json');
  assert.throws(()=>evidenceExport({},null,[],''));
});

test('default case requires valid reference continuity and complete prior windows when possible',()=>{
  const insufficient={symbol:'000001',date:20260701,screened:true,reference_gap:null,volume_prior5_count:0,spread_prior5_count:0,imbalance_prior5_count:0};
  const supported={...insufficient,symbol:'600001',date:20260730,reference_gap:.001,volume_prior5_count:5,spread_prior5_count:5,imbalance_prior5_count:5};
  assert.equal(selectInitialEvent([insufficient,supported]),supported);
  assert.equal(selectInitialEvent([insufficient,supported],'000001_20260701'),insufficient);
  assert.equal(selectInitialEvent([]),null);
});
