export const PAGE_SIZE=8;
export const isFiniteNumber=value=>typeof value==='number'&&Number.isFinite(value);
export function decodeRow(columns,row){return Object.fromEntries(columns.map((key,index)=>[key,row[index]]));}
export const eventId=event=>`${event.symbol}_${event.date}`;
export function filterEvents(events,{search='',date='',quality='all',sort='date_desc'}={}){
  const query=String(search).trim();
  const rows=events.filter(event=>(!query||String(event.symbol).includes(query))&&(!date||String(event.date)===String(date))&&(quality==='all'||(quality==='clean'?event.screened:!event.screened)));
  const fallback=(a,b)=>Number(b.date)-Number(a.date)||String(a.symbol).localeCompare(String(b.symbol));
  if(sort==='decline')rows.sort((a,b)=>(isFiniteNumber(a.return)?a.return:Infinity)-(isFiniteNumber(b.return)?b.return:Infinity)||fallback(a,b));
  else if(sort==='volume')rows.sort((a,b)=>(isFiniteNumber(b.volume_ratio)?b.volume_ratio:-Infinity)-(isFiniteNumber(a.volume_ratio)?a.volume_ratio:-Infinity)||fallback(a,b));
  else rows.sort(fallback);
  return rows;
}
export function historyThroughEvent(rows,event){return rows.filter(row=>row.symbol===event.symbol&&Number(row.date)<=Number(event.date)).sort((a,b)=>Number(a.date)-Number(b.date));}
export function selectInitialEvent(events,preferredId){
  return events.find(event=>eventId(event)===preferredId)||events.find(event=>event.screened&&isFiniteNumber(event.reference_gap)&&Math.abs(event.reference_gap)<=.01&&['volume','spread','imbalance'].every(key=>event[`${key}_prior5_count`]===5))||events.find(event=>event.screened)||events[0]||null;
}
export function evidenceExport(data,event,history,seriesFile){
  if(!event)throw Error('请选择一个事件');
  return {schema_version:1,kind:'historical_daily_event_evidence',simulated:false,event:{...event},history:historyThroughEvent(history,event),provenance:{dataset:'data/study.json',series_file:seriesFile,source_metadata:data.metadata,source_summary:data.summary,event_precision:'source_full_precision',history_representation:'display_precision_projection',history_rounding:data.metadata?.history_display_rounding},limitations:['日终历史观察，不是日内预警。','样本中位数不是官方指数或行业基准。','统计比较不构成新闻核实、原因概率或因果分解。','未完成复权和公司行动核实；口径筛查不等于完整数据认证。']};
}
