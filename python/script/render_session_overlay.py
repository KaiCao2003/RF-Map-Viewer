#!/usr/bin/env python3
"""Render an exported session overlay as an offline, self-contained HTML report."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


DOCUMENT = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root {color-scheme:light; font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#182434;background:#fff}
* {box-sizing:border-box} body {margin:0;background:#fff} main {max-width:1780px;margin:auto;padding:26px 30px 36px}
header {display:flex;align-items:flex-start;justify-content:space-between;gap:20px;margin-bottom:18px}
h1 {font-size:25px;letter-spacing:-.6px;line-height:1.2;margin:5px 0 7px} p {margin:0;color:#536276;line-height:1.5;font-size:13px}
.eyebrow {font-size:11px;font-weight:750;letter-spacing:1.5px;color:#526279;text-transform:uppercase}
.pill {font-size:12px;font-weight:650;background:#edf4ff;color:#184e92;padding:8px 12px;border-radius:20px;white-space:nowrap}
.controls {display:flex;gap:9px;align-items:center;flex-wrap:wrap;padding:12px 0;border-top:1px solid #dce3ec;border-bottom:1px solid #dce3ec}
button,select,input {font:inherit;color:#182434} button,select {border:1px solid #c7d1df;background:#fff;border-radius:7px;padding:8px 11px;font-size:12px;cursor:pointer}
button:hover {background:#f0f5fa} button.primary {color:#fff;background:#1c5b9c;border-color:#1c5b9c;min-width:83px}
label {font-size:12px;color:#485a70;display:flex;align-items:center;gap:7px} .spacer {flex:1}
#timeInput {width:94px;padding:7px 8px;border:1px solid #c7d1df;border-radius:6px;font-size:12px;background:#fff}
.stats {display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:16px;margin:18px 0}
.stat {border-left:2px solid #d7e2ef;padding-left:11px}.stat small {font-size:10px;letter-spacing:.8px;text-transform:uppercase;color:#5c6b7d;display:block;margin-bottom:5px}.stat strong {font-size:16px;font-weight:650;font-variant-numeric:tabular-nums}
.plots {display:grid;grid-template-columns:minmax(340px,1.04fr) minmax(520px,1.7fr);gap:18px}
.panel {min-width:0;border:1px solid #dce3ec;border-radius:10px;overflow:hidden;background:#fff}
.panel-head {padding:14px 16px 8px;display:flex;justify-content:space-between;align-items:baseline;gap:8px}.panel h2 {font-size:14px;margin:0;font-weight:700}.panel-head span {font-size:11px;color:#5a697d}
.plot-wrap {padding:0 8px}.plot-wrap canvas {display:block;width:100%;height:430px;background:#fff}
.heatmaps {display:grid;grid-template-columns:1fr 1fr;gap:2px}.heatmaps canvas {display:block;width:100%;height:370px;background:#fff}
.legend {padding:0 16px 14px;display:flex;gap:12px;flex-wrap:wrap;color:#435268;font-size:10px;line-height:1.5}.legend span {display:inline-flex;align-items:center;gap:5px}.line {width:16px;height:3px;display:inline-block;background:var(--ink);border-radius:3px}.dot {width:7px;height:7px;border-radius:50%;background:var(--ink);display:inline-block}
.note {font-size:11px;color:#536276;padding:2px 16px 13px;line-height:1.5}
.timeline {margin-top:16px}.timeline canvas {width:100%;height:105px;display:block;background:#fff;cursor:crosshair;touch-action:none}
.timeline.local canvas {height:125px}.timeline .panel-head {padding-bottom:0}
input[type=range] {width:100%;accent-color:#1c5b9c;cursor:pointer;margin:0;display:block} .scrubber {padding:0 47px 10px 49px}
details {margin-top:18px;border-top:1px solid #dce3ec;padding-top:13px;font-size:12px;color:#536276}summary {cursor:pointer;color:#26394f;font-weight:650}details p {margin-top:8px;font-size:12px}details ul {padding-left:20px;line-height:1.65;margin-bottom:0}
footer {font-size:11px;color:#657489;margin-top:20px} .status {font-variant-numeric:tabular-nums}
@media(max-width:1000px){main{padding:18px}.plots{grid-template-columns:1fr}.plot-wrap canvas{height:450px}.heatmaps canvas{height:350px}.stats{grid-template-columns:repeat(3,1fr)}header{gap:10px}h1{font-size:22px}}
@media(max-width:550px){main{padding:12px}.heatmaps{grid-template-columns:1fr}.stats{gap:12px}.stat strong{font-size:13px}.pill{font-size:10px}.controls{gap:6px}}
</style>
</head>
<body><main>
<header><div><div class="eyebrow">Synchronized session geometry</div><h1 id="title"></h1><p id="subtitle"></p></div><div class="pill" id="unitTag"></div></header>
<div class="controls">
<button id="play" class="primary" aria-label="Play or pause">▶ Play</button>
<button id="prev" aria-label="Previous preview frame">← Frame</button><button id="next" aria-label="Next preview frame">Frame →</button>
<label>Speed <select id="speed"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label>
<label>Local window <select id="window"><option value="2">2 s</option><option value="10" selected>10 s</option><option value="30">30 s</option><option value="60">60 s</option></select></label>
<span class="spacer"></span><label>Time <input id="timeInput" type="number" step="0.01" aria-label="Session time in seconds"> s</label><button id="snapshot">Save frame PNG</button>
</div>
<div class="stats">
<div class="stat"><small>Session time</small><strong id="timeStat"></strong></div>
<div class="stat"><small>Motive frame · 30 Hz display</small><strong id="frameStat"></strong></div>
<div class="stat"><small>Head direction</small><strong id="headingStat"></strong></div>
<div class="stat"><small>VS trial · state</small><strong id="trialStat"></strong></div>
<div class="stat"><small>VS center · eye bearing CCW</small><strong id="bearingStat"></strong></div>
<div class="stat"><small>Unit spikes · ±50 ms</small><strong id="spikeStat"></strong></div>
</div>
<div class="plots">
<section class="panel"><div class="panel-head"><h2>World top view</h2><span>Calibrated coordinates · cm</span></div><div class="plot-wrap"><canvas id="world" aria-label="Mouse position, heading, visual stimulus, and boundary geometry"></canvas></div><div class="legend"><span><i class="line" style="--ink:#344457"></i>Screen</span><span><i class="line" style="--ink:#16a1a2"></i>Head + trail</span><span><i class="dot" style="--ink:#d95129"></i>Eye</span><span><i class="line" style="--ink:#c17b12"></i>VS + eye bearing</span><span><i class="line" style="--ink:#7462ab"></i>Inner/Outer EBC model</span></div></section>
<section class="panel"><div class="panel-head"><h2>EBC tuning × current geometry</h2><span>Unit <span id="ebcUnit"></span> · session-wide firing rate</span></div><div class="heatmaps"><canvas id="heat0" aria-label="Inner EBC tuning with current distance curve"></canvas><canvas id="heat1" aria-label="Outer EBC tuning with current distance curve"></canvas></div><div class="legend"><span><i class="line" style="--ink:#172333"></i>Current headplate-origin distance d(θ)</span><span><i class="line" style="--ink:#c17b12"></i>VS bearing at headplate</span><span>Positive bearing = left / CCW</span></div><p class="note" id="geometryNote"></p></section>
</div>
<section class="panel timeline local"><div class="panel-head"><h2>Local spike + visual-stimulus timeline</h2><span id="localLabel"></span></div><canvas id="local" aria-label="Local stimulus states and spike times"></canvas></section>
<section class="panel timeline"><div class="panel-head"><h2>Full session</h2><span id="sessionLabel"></span></div><canvas id="session" aria-label="Complete session timeline and unit spike density"></canvas><div class="scrubber"><input id="scrub" type="range" min="0" max="1" step="0.001" aria-label="Scrub full session"></div></section>
<details><summary>Geometry, timing, and sources</summary><div id="methods"></div></details>
<footer>Drag either timeline to seek. Arrow keys step one 30 Hz preview frame; Space plays or pauses. Original camera frame IDs are shown above. All times use the exported session clock.</footer>
</main>
<script id="overlay-data" type="application/json">__DATA__</script>
<script>
'use strict';
const DATA=JSON.parse(document.getElementById('overlay-data').textContent);
// The exported schema is normalized once; plotting code uses calibrated cm and CCW degrees.
const D=normalize(DATA);
const commonRateMax=Math.max(...D.ebc.flatMap(model=>model.rate_hz.flat()).filter(Number.isFinite),1e-9);
const byId=id=>document.getElementById(id);
const C={screen:'#344457',head:'#16a1a2',eye:'#d95129',vs:'#c17b12',boundary:'#7462ab',ink:'#172333',muted:'#66758a',grid:'#e5eaf0'};
const rad=Math.PI/180, wrap=a=>Number.isFinite(a)?((a+180)%360+360)%360-180:NaN;
const fmt=(v,n=1)=>Number.isFinite(v)?v.toFixed(n):'—';
const times=D.frames.time_s;
let frame=0,current=times[0],playing=false,lastPaint=0,lastTick=null,localWidth=10;
const start=times[0],end=times[times.length-1],duration=end-start;
const surfaces={};
let heatCache=[],sessionCache=null,resizePending=true;

function normalize(data) {
  data.frames.head_x_cm=data.frames.x_cm;
  data.frames.head_y_cm=data.frames.y_cm;
  data.trial_on_s=data.trials.map(trial=>trial.on_s);
  data.trials.forEach((trial,index)=>{trial.id=index+1;trial.end_s=trial.off_s;});
  return data;
}

function lowerBound(a,v) {let lo=0,hi=a.length;while(lo<hi){const mid=(lo+hi)>>>1;if(a[mid]<v)lo=mid+1;else hi=mid;}return lo;}
function nearest(a,v){const i=lowerBound(a,v);if(i===0)return 0;if(i===a.length)return a.length-1;return v-a[i-1]<=a[i]-v?i-1:i;}
function frameValue(name,i=frame){return D.frames[name][i];}
function point(name,i=frame){return [frameValue(name+'_x_cm',i),frameValue(name+'_y_cm',i)];}
function finitePoint(p){return p.every(Number.isFinite);}
function makeCanvas(id){const el=byId(id),rect=el.getBoundingClientRect(),scale=window.devicePixelRatio||1;el.width=Math.round(rect.width*scale);el.height=Math.round(rect.height*scale);const ctx=el.getContext('2d');ctx.setTransform(scale,0,0,scale,0,0);return {el,ctx,w:rect.width,h:rect.height};}
function clear(s){s.ctx.fillStyle='#fff';s.ctx.fillRect(0,0,s.w,s.h);s.ctx.font='11px system-ui';s.ctx.textBaseline='alphabetic';s.ctx.lineWidth=1;s.ctx.setLineDash([]);}
function text(ctx,str,x,y,color=C.ink,align='left',size=11){ctx.font=size+'px system-ui';ctx.fillStyle=color;ctx.textAlign=align;ctx.fillText(str,x,y);}
function line(ctx,x1,y1,x2,y2,color,width=1,dash=[]){ctx.beginPath();ctx.setLineDash(dash);ctx.lineWidth=width;ctx.strokeStyle=color;ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();ctx.setLineDash([]);}
function circle(ctx,x,y,r,color,fill=false,width=1){ctx.beginPath();ctx.arc(x,y,r,0,2*Math.PI);ctx.lineWidth=width;if(fill){ctx.fillStyle=color;ctx.fill();}else{ctx.strokeStyle=color;ctx.stroke();}}
function niceStep(span,target=6){const raw=span/target,p=10**Math.floor(Math.log10(raw)),v=raw/p;return (v<=1?1:v<=2?2:v<=5?5:10)*p;}
function ticks(min,max,target=6){const step=niceStep(max-min,target),out=[];for(let x=Math.ceil(min/step)*step;x<=max+step*1e-5;x+=step)out.push(x);return out;}
function currentTrial(){const i=lowerBound(D.trial_on_s,current+1e-9)-1;return i>=0&&current<D.trials[i].end_s?D.trials[i]:null;}
function stimulusPoints(trial){if(!trial)return null;const a=trial.center_world_deg*rad,w=trial.width_deg*rad/2,c=D.screen.center_cm,r=D.screen.radius_cm;return [-w,0,w].map(offset=>[c[0]+r*Math.cos(a+offset),c[1]+r*Math.sin(a+offset)]);}
function bearings(trial,origin='eye'){const pts=stimulusPoints(trial),eye=point(origin),hd=frameValue('heading_deg');return pts&&finitePoint(eye)&&Number.isFinite(hd)?pts.map(p=>wrap(Math.atan2(p[1]-eye[1],p[0]-eye[0])/rad-hd)):null;}
function trialActive(trial){return trial&&trial.luminance>.5&&current>=trial.on_s&&current<trial.off_s;}
function distanceToCircle(origin,angle,center,radius){const ux=Math.cos(angle),uy=Math.sin(angle),dx=origin[0]-center[0],dy=origin[1]-center[1],b=dx*ux+dy*uy,disc=b*b-(dx*dx+dy*dy-radius*radius);if(disc<0)return null;const root=Math.sqrt(disc),near=-b-root,far=-b+root;return near>=0?near:far>=0?far:null;}

function renderWorld(){
  const s=surfaces.world,{ctx,w,h}=s;clear(s);
  const screen=D.screen,extent=[screen.center_cm[0]-screen.radius_cm,screen.center_cm[0]+screen.radius_cm,screen.center_cm[1]-screen.radius_cm,screen.center_cm[1]+screen.radius_cm];
  for(const model of D.ebc){const c=model.center_cm,r=model.radius_cm;extent[0]=Math.min(extent[0],c[0]-r);extent[1]=Math.max(extent[1],c[0]+r);extent[2]=Math.min(extent[2],c[1]-r);extent[3]=Math.max(extent[3],c[1]+r);}
  const span=Math.max(extent[1]-extent[0],extent[3]-extent[2])*1.12,scale=Math.min(w-85,h-67)/span,cx=(extent[0]+extent[1])/2,cy=(extent[2]+extent[3])/2;
  const px=x=>w/2+12+(x-cx)*scale,py=y=>h/2-12-(y-cy)*scale;
  const bounds={x0:w/2+12-span*scale/2,x1:w/2+12+span*scale/2,y0:h/2-12-span*scale/2,y1:h/2-12+span*scale/2};
  for(const x of ticks(cx-span/2,cx+span/2)){line(ctx,px(x),bounds.y0,px(x),bounds.y1,C.grid);text(ctx,fmt(x,0),px(x),bounds.y1+16,C.muted,'center',10);}
  for(const y of ticks(cy-span/2,cy+span/2)){line(ctx,bounds.x0,py(y),bounds.x1,py(y),C.grid);text(ctx,fmt(y,0),bounds.x0-7,py(y)+3,C.muted,'right',10);}
  text(ctx,'World X (cm)',(bounds.x0+bounds.x1)/2,h-9,C.muted,'center',10);ctx.save();ctx.translate(14,h/2);ctx.rotate(-Math.PI/2);text(ctx,'World Y (cm)',0,0,C.muted,'center',10);ctx.restore();
  D.ebc.forEach((model,index)=>{ctx.setLineDash(index?[5,4]:[2,3]);circle(ctx,px(model.center_cm[0]),py(model.center_cm[1]),model.radius_cm*scale,C.boundary,false,1.2);ctx.setLineDash([]);});
  circle(ctx,px(screen.center_cm[0]),py(screen.center_cm[1]),screen.radius_cm*scale,C.screen,false,2);
  const eye=point('eye'),head=point('head'),heading=frameValue('heading_deg'),trial=currentTrial(),pts=stimulusPoints(trial),active=trialActive(trial);
  if(trial&&pts){const a=trial.center_world_deg*rad,half=trial.width_deg*rad/2;ctx.beginPath();ctx.arc(px(screen.center_cm[0]),py(screen.center_cm[1]),screen.radius_cm*scale,-a-half,-a+half);ctx.strokeStyle=active?C.vs:'#aeb9c7';ctx.lineWidth=active?7:3;ctx.setLineDash(active?[]:[3,3]);ctx.stroke();ctx.setLineDash([]);if(finitePoint(eye)){for(const p of [pts[0],pts[2]])line(ctx,px(eye[0]),py(eye[1]),px(p[0]),py(p[1]),active?C.vs:'#b7bec9',1.2,active?[]:[3,4]);}}
  const trailStart=lowerBound(times,current-2);ctx.beginPath();let begun=false;for(let i=trailStart;i<=frame;i++){const p=point('head',i);if(!finitePoint(p)){begun=false;continue;}if(begun)ctx.lineTo(px(p[0]),py(p[1]));else ctx.moveTo(px(p[0]),py(p[1]));begun=true;}ctx.lineWidth=2;ctx.strokeStyle='#83cbca';ctx.stroke();
  if(finitePoint(head)){
    circle(ctx,px(head[0]),py(head[1]),4,C.head,true);
    if(Number.isFinite(heading)){const a=heading*rad,len=6,tip=[head[0]+len*Math.cos(a),head[1]+len*Math.sin(a)];line(ctx,px(head[0]),py(head[1]),px(tip[0]),py(tip[1]),C.head,2.5);ctx.beginPath();ctx.moveTo(px(tip[0]),py(tip[1]));ctx.lineTo(px(tip[0]-1.4*Math.cos(a-.5)),py(tip[1]-1.4*Math.sin(a-.5)));ctx.lineTo(px(tip[0]-1.4*Math.cos(a+.5)),py(tip[1]-1.4*Math.sin(a+.5)));ctx.closePath();ctx.fillStyle=C.head;ctx.fill();}
    if(Number.isFinite(heading))for(const model of D.ebc)for(let bearing=0;bearing<360;bearing+=45){const a=(heading+bearing)*rad,dist=distanceToCircle(head,a,model.center_cm,model.radius_cm);if(dist!==null){const endpoint=[head[0]+dist*Math.cos(a),head[1]+dist*Math.sin(a)];line(ctx,px(head[0]),py(head[1]),px(endpoint[0]),py(endpoint[1]),'#c8bedb',.7,[2,5]);circle(ctx,px(endpoint[0]),py(endpoint[1]),1.6,C.boundary,true);}}
  }
  if(finitePoint(eye))circle(ctx,px(eye[0]),py(eye[1]),3.2,C.eye,true);
  text(ctx,active?'White bar':trial?'Gray trial: assigned location (no contrast)':'Outside VS trials',bounds.x0+4,bounds.y0+13,active?'#96600f':C.muted,'left',10);
  if(!finitePoint(head)||!Number.isFinite(heading))text(ctx,'Tracking unavailable for this frame',w/2,h/2,C.eye,'center',12);
}

const colorStops=[[0,[247,251,254]],[.2,[206,230,243]],[.45,[112,176,209]],[.7,[46,123,167]],[1,[11,56,104]]];
function rateColor(t){t=Math.max(0,Math.min(1,t));for(let i=1;i<colorStops.length;i++){if(t<=colorStops[i][0]){const [a,ca]=colorStops[i-1],[b,cb]=colorStops[i],u=(t-a)/(b-a);return 'rgb('+ca.map((v,j)=>Math.round(v+(cb[j]-v)*u)).join(',')+')';}}return '#0b3868';}
function heatGeometry(s,model){const left=52,right=s.w-21,top=42,bottom=s.h-70,be=model.bearing_edges_deg,de=model.distance_edges_cm;return {left,right,top,bottom,be,de,bmin:-180,bmax:180,dmin:de[0],dmax:de.at(-1),x:d=>left+(d-de[0])/(de.at(-1)-de[0])*(right-left),y:b=>bottom-(b+180)/360*(bottom-top)};}
function buildHeat(index){
  const s=surfaces['heat'+index],model=D.ebc[index],g=heatGeometry(s,model),canvas=document.createElement('canvas');canvas.width=s.el.width;canvas.height=s.el.height;const ctx=canvas.getContext('2d'),scale=window.devicePixelRatio||1;ctx.setTransform(scale,0,0,scale,0,0);clear({...s,ctx});
  const vmax=commonRateMax;
  ctx.save();ctx.beginPath();ctx.rect(g.left,g.top,g.right-g.left,g.bottom-g.top);ctx.clip();
  model.rate_hz.forEach((row,bi)=>row.forEach((value,di)=>{ctx.fillStyle=Number.isFinite(value)?rateColor(value/vmax):'#f0f0f0';const x0=g.x(g.de[di]),x1=g.x(g.de[di+1]),y0=g.y(g.be[bi]),y1=g.y(g.be[bi+1]);ctx.fillRect(x0,y1,x1-x0+.5,y0-y1+.5);}));ctx.restore();
  ctx.strokeStyle='#9caabc';ctx.lineWidth=.7;ctx.strokeRect(g.left,g.top,g.right-g.left,g.bottom-g.top);
  for(const b of [-180,-90,0,90,180])if(b>=g.bmin&&b<=g.bmax){line(ctx,g.left-4,g.y(b),g.left,g.y(b),C.muted);text(ctx,String(b),g.left-7,g.y(b)+3,C.muted,'right',10);}
  for(const d of ticks(g.dmin,g.dmax,4)){line(ctx,g.x(d),g.bottom,g.x(d),g.bottom+4,C.muted);text(ctx,fmt(d,0),g.x(d),g.bottom+17,C.muted,'center',10);}
  text(ctx,model.name[0].toUpperCase()+model.name.slice(1)+' EBC',g.left,23,C.ink,'left',13);text(ctx,'EBC distance (model cm)',(g.left+g.right)/2,g.bottom+34,C.muted,'center',10);ctx.save();ctx.translate(14,(g.top+g.bottom)/2);ctx.rotate(-Math.PI/2);text(ctx,'Bearing (°; left +)',0,0,C.muted,'center',10);ctx.restore();
  const barX=g.left,barY=s.h-16,barW=Math.min(130,(g.right-g.left)*.56);for(let i=0;i<barW;i++){ctx.fillStyle=rateColor(i/barW);ctx.fillRect(barX+i,barY,1.2,6);}text(ctx,'0',barX,barY-3,C.muted,'left',9);text(ctx,fmt(vmax,1)+' Hz',barX+barW,barY-3,C.muted,'right',9);
  return {canvas,g};
}
function renderHeat(index){
  const s=surfaces['heat'+index],{ctx,w,h}=s,model=D.ebc[index],cache=heatCache[index],g=cache.g;clear(s);ctx.drawImage(cache.canvas,0,0,w,h);
  const head=point('head'),heading=frameValue('heading_deg'),vs=bearings(currentTrial(),'head');
  ctx.save();ctx.beginPath();ctx.rect(g.left,g.top,g.right-g.left,g.bottom-g.top);ctx.clip();
  if(vs){for(const bearing of [vs[0],vs[2]])for(const b of [bearing-360,bearing,bearing+360])if(b>=g.bmin&&b<=g.bmax)line(ctx,g.left,g.y(b),g.right,g.y(b),trialActive(currentTrial())?C.vs:'#b7bec9',1.4,trialActive(currentTrial())?[5,3]:[2,5]);}
  if(finitePoint(head)&&Number.isFinite(heading)){
    ctx.beginPath();let connected=false;for(let b=g.bmin;b<=g.bmax+.01;b+=1){const dist=distanceToCircle(head,(heading+b)*rad,model.center_cm,model.radius_cm),modelDistance=dist===null?null:dist*model.model_cm_per_world_cm;if(modelDistance===null){connected=false;continue;}if(connected)ctx.lineTo(g.x(modelDistance),g.y(b));else ctx.moveTo(g.x(modelDistance),g.y(b));connected=true;}ctx.strokeStyle='#fff';ctx.lineWidth=4;ctx.stroke();ctx.strokeStyle=C.ink;ctx.lineWidth=1.8;ctx.stroke();
  }
  ctx.restore();
}

function timelineBase(s,range,yBase){const {ctx,w,h}=s,left=49,right=w-47,x=t=>left+(t-range[0])/(range[1]-range[0])*(right-left);line(ctx,left,yBase,right,yBase,'#b4bfce');for(const t of ticks(range[0],range[1],w/115)){line(ctx,x(t),yBase,x(t),yBase+4,C.muted);text(ctx,fmt(t,range[1]-range[0]<5?1:0),x(t),yBase+17,C.muted,'center',10);}return {left,right,x};}
function localRange(){return [Math.max(start,Math.min(end-localWidth,current-localWidth/2)),Math.min(end,Math.max(start+localWidth,current+localWidth/2))];}
function renderLocal(){
  const s=surfaces.local,{ctx,w,h}=s,range=localRange();clear(s);const {left,right,x}=timelineBase(s,range,h-29);text(ctx,'VS',left-10,37,C.muted,'right',10);text(ctx,'Spikes',left-10,69,C.muted,'right',10);
  ctx.fillStyle='#e5e8ed';ctx.fillRect(left,25,right-left,16);let i=Math.max(0,lowerBound(D.trial_on_s,range[0])-1);for(;i<D.trials.length&&D.trials[i].on_s<range[1];i++){const tr=D.trials[i],a=Math.max(range[0],tr.on_s),b=Math.min(range[1],tr.off_s);if(b>a&&tr.luminance>.5){ctx.fillStyle='#dbab54';ctx.fillRect(x(a),25,Math.max(.7,x(b)-x(a)),16);}}
  for(let j=lowerBound(D.spikes_s,range[0]);j<D.spikes_s.length&&D.spikes_s[j]<=range[1];j++)line(ctx,x(D.spikes_s[j]),53,x(D.spikes_s[j]),77,C.ink,1);
  line(ctx,x(current),18,x(current),h-28,'#c24330',1.4);text(ctx,fmt(current,3)+' s',Math.max(left+25,Math.min(right-25,x(current))),13,'#a8382a','center',10);
  byId('localLabel').textContent='Gold = white stimulus · ticks = unit spikes · '+fmt(range[1]-range[0],0)+' s window';
}
function buildSession(){
  const s=surfaces.session,{w,h}=s,canvas=document.createElement('canvas');canvas.width=s.el.width;canvas.height=s.el.height;const ctx=canvas.getContext('2d'),scale=window.devicePixelRatio||1;ctx.setTransform(scale,0,0,scale,0,0);clear({...s,ctx});const {left,right,x}=timelineBase({...s,ctx},[start,end],h-29),bins=Math.max(1,Math.round(right-left)),counts=new Uint32Array(bins);for(const spike of D.spikes_s)if(spike>=start&&spike<=end)counts[Math.min(bins-1,Math.floor((spike-start)/duration*bins))]++;const max=Math.max(...counts,1);ctx.fillStyle='#7798b8';counts.forEach((v,i)=>ctx.fillRect(left+i,70-v/max*38,1,Math.max(v>0?1:0,v/max*38)));if(D.trials.length){line(ctx,x(D.trials[0].on_s),22,x(D.trials.at(-1).end_s),22,'#d3ad69',4);}text(ctx,'VS',left-10,25,C.muted,'right',10);text(ctx,'Spikes',left-10,57,C.muted,'right',10);return canvas;
}
function renderSession(){const s=surfaces.session,{ctx,w,h}=s;clear(s);ctx.drawImage(sessionCache,0,0,w,h);const x=t=>49+(t-start)/duration*(w-96),range=localRange();ctx.fillStyle='rgba(28,91,156,.10)';ctx.fillRect(x(range[0]),13,Math.max(2,x(range[1])-x(range[0])),h-42);line(ctx,x(current),12,x(current),h-29,'#c24330',1.5);}
function renderStats(){const tr=currentTrial(),bs=bearings(tr),count=lowerBound(D.spikes_s,current+.05)-lowerBound(D.spikes_s,current-.05);byId('timeStat').textContent=fmt(current,3)+' s';byId('frameStat').textContent=String(frameValue('frame_id'))+' · '+(frame+1).toLocaleString()+'/'+times.length.toLocaleString();byId('headingStat').textContent=fmt(wrap(frameValue('heading_deg')),1)+'°';byId('trialStat').textContent=tr?tr.id+' · '+(trialActive(tr)?'WHITE':'GRAY'):'—';byId('bearingStat').textContent=bs?fmt(bs[1],1)+'°':'—';byId('spikeStat').textContent=count.toString();byId('scrub').value=String(current);if(document.activeElement!==byId('timeInput'))byId('timeInput').value=fmt(current,3);}
function render(){if(resizePending){for(const id of ['world','heat0','heat1','local','session'])surfaces[id]=makeCanvas(id);heatCache=D.ebc.map((_,i)=>buildHeat(i));sessionCache=buildSession();resizePending=false;}renderStats();renderWorld();D.ebc.forEach((_,i)=>renderHeat(i));renderLocal();renderSession();}
function seek(time,snap=false){current=Math.max(start,Math.min(end,time));frame=nearest(times,current);if(snap)current=times[frame];render();}
function setPlaying(value){playing=value;lastTick=null;byId('play').textContent=playing?'Ⅱ Pause':'▶ Play';}
function animate(stamp){if(playing){if(lastTick!==null){current=Math.min(end,current+(stamp-lastTick)/1000*Number(byId('speed').value));frame=nearest(times,current);}lastTick=stamp;if(stamp-lastPaint>30||current===end){render();lastPaint=stamp;}if(current===end)setPlaying(false);}requestAnimationFrame(animate);}
function step(n){setPlaying(false);frame=Math.max(0,Math.min(times.length-1,frame+n));seek(times[frame],true);}
function bindTimeline(id,rangeFn){const canvas=byId(id);let down=false;const apply=event=>{const r=canvas.getBoundingClientRect(),range=rangeFn(),fraction=Math.max(0,Math.min(1,(event.clientX-r.left-49)/(r.width-96)));seek(range[0]+fraction*(range[1]-range[0]));};canvas.addEventListener('pointerdown',event=>{down=true;setPlaying(false);canvas.setPointerCapture(event.pointerId);apply(event);});canvas.addEventListener('pointermove',event=>{if(down)apply(event);});canvas.addEventListener('pointerup',()=>down=false);canvas.addEventListener('pointercancel',()=>down=false);}
function saveSnapshot(){
  const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');
  canvas.width=1800;canvas.height=950;
  ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);
  text(ctx,D.meta.session+' · unit '+D.meta.unit_id,40,48,C.ink,'left',28);
  text(ctx,'Time '+fmt(current,3)+' s · Motive frame '+frameValue('frame_id')+' · head direction '+fmt(wrap(frameValue('heading_deg')),1)+'°',40,78,C.muted,'left',16);
  // Preserve each plot's aspect ratio so world circles remain circles in exports.
  const fit=(surface,x,y,w,h)=>{const scale=Math.min(w/surface.w,h/surface.h),dw=surface.w*scale,dh=surface.h*scale;ctx.drawImage(surface.el,x+(w-dw)/2,y+(h-dh)/2,dw,dh);};
  fit(surfaces.world,20,100,690,610);
  fit(surfaces.heat0,730,120,510,540);
  fit(surfaces.heat1,1260,120,510,540);
  text(ctx,'World: calibrated cm. EBC: model distance; dark curve = current headplate-origin geometry.',40,738,C.muted,'left',15);
  text(ctx,'World VS rays use eye origin; EBC VS marks use headplate origin. Positive bearing = left / CCW.',40,764,C.muted,'left',15);
  fit(surfaces.local,20,790,1760,140);
  const a=document.createElement('a');
  a.download=D.meta.session.replaceAll('/','_')+'_unit'+D.meta.unit_id+'_'+fmt(current,3)+'s.png';
  a.href=canvas.toDataURL('image/png');a.click();
}
byId('title').textContent=D.meta.session+' · mouse, VS & EBC';byId('unitTag').textContent='UNIT '+D.meta.unit_id;byId('ebcUnit').textContent=D.meta.unit_id;byId('subtitle').textContent='Screen calibration, tracked head and eye, stimulus bearing, and existing EBC tuning on one clock.';
byId('subtitle').textContent='World: calibrated cm · EBC: saved model distances; headplate origin · '+D.meta.time_reference;
byId('geometryNote').textContent=D.meta.geometry_note+' Gray trials show assigned location with no visible bar contrast.';
byId('sessionLabel').textContent=fmt(duration,1)+' s · '+D.trials.length.toLocaleString()+' trials · '+D.spikes_s.length.toLocaleString()+' spikes';
const methods=byId('methods');for(const key of ['angle_note','bearing_display_note','stimulus_note','timing_note','eye_model','body_source','eye_position_source','calibration_path']){if(!D.meta[key])continue;const p=document.createElement('p');p.textContent=key.replaceAll('_',' ')+': '+D.meta[key];methods.appendChild(p);}for(const model of D.ebc){const p=document.createElement('p');p.textContent=model.name+' EBC: '+model.source_path+'; '+fmt(model.model_cm_per_world_cm,6)+' model cm per calibrated world cm.';methods.appendChild(p);}
byId('scrub').min=start;byId('scrub').max=end;byId('timeInput').min=start;byId('timeInput').max=end;
byId('play').addEventListener('click',()=>{if(!playing&&current>=end)seek(start);setPlaying(!playing);});byId('prev').addEventListener('click',()=>step(-1));byId('next').addEventListener('click',()=>step(1));byId('scrub').addEventListener('input',event=>{setPlaying(false);seek(Number(event.target.value));});
const seekTimeInput=event=>{const t=event.target.valueAsNumber;if(Number.isFinite(t)){setPlaying(false);seek(t);}};
byId('timeInput').addEventListener('input',seekTimeInput);
byId('timeInput').addEventListener('change',seekTimeInput);
byId('window').addEventListener('change',event=>{localWidth=Number(event.target.value);render();});byId('snapshot').addEventListener('click',saveSnapshot);
let dragLocalRange=null;byId('local').addEventListener('pointerdown',()=>{dragLocalRange=localRange();});bindTimeline('local',()=>dragLocalRange??localRange());bindTimeline('session',()=>[start,end]);
document.addEventListener('keydown',event=>{if(['INPUT','SELECT'].includes(document.activeElement.tagName))return;if(event.code==='Space'){event.preventDefault();setPlaying(!playing);}else if(event.key==='ArrowLeft'){event.preventDefault();step(-1);}else if(event.key==='ArrowRight'){event.preventDefault();step(1);}});
window.addEventListener('resize',()=>{resizePending=true;render();});
window.setOverlayTime=seconds=>{setPlaying(false);seek(Number(seconds));return frame;};
seek(D.meta.preview_start_s??D.trials[0]?.on_s??start,true);requestAnimationFrame(animate);
</script></body></html>
"""


def render_report(input_path: Path, output_path: Path) -> None:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    title = f"{data['meta']['session']} · unit {data['meta']['unit_id']} overlay"
    # Escape '<' so source paths and notes cannot terminate the JSON script element.
    encoded = json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    encoded = encoded.replace("<", "\\u003c")
    document = DOCUMENT.replace("__TITLE__", html.escape(title)).replace("__DATA__", encoded)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Exported session-overlay JSON")
    parser.add_argument("output", type=Path, help="Self-contained output HTML")
    args = parser.parse_args()
    render_report(args.input, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
