"use strict";
// Exercise the shipped pointer handlers without a browser or dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,"../static/cctv/workflow.js"),"utf8");
const captures = new Set();
const context = {workflowBusy:false, workflowUploading:false, workflowRois:{},
  workflowCanvas:{style:{}, getBoundingClientRect:()=>({left:0,top:0,width:320,height:180}),
    setPointerCapture:id=>captures.add(id), hasPointerCapture:id=>captures.has(id),
    releasePointerCapture:id=>captures.delete(id)},
  workflowCamera:()=>"CAM_1", workflowDraw:()=>{}, workflowProblem:()=>{}};
vm.runInNewContext(source.slice(source.indexOf("let workflowDrag="), source.indexOf('$("workflow-undo").onclick=')),context);
const canvas=context.workflowCanvas;
const event=(x,y,type="pointerup",pointerId=1)=>({clientX:x,clientY:y,type,pointerId,button:0,detail:1,preventDefault:()=>{}});
const click=(x,y)=>canvas.onclick(event(x,y));
const drag=(x,y,toX,toY,type="pointerup")=>{
  canvas.onpointerdown(event(x,y,"pointerdown"));
  canvas.onpointermove(event(toX,toY,"pointermove"));
  canvas[type==="pointerup" ? "onpointerup" : "onpointercancel"](event(toX,toY,type));
  click(toX,toY); // Browsers may emit a click after release; never add a vertex.
};
click(32,18); click(288,18); click(288,162);
const points=context.workflowRois.CAM_1;
drag(32,18,64,36);
assert.equal(points.length,3); assert.equal(points[0][0],0.2); assert.equal(points[0][1],0.2);
assert.equal(captures.size,0);
drag(64,36,288,18); assert.equal(points[0][0],0.2); // Duplicate release rolls back.
drag(64,36,80,50,"pointercancel"); assert.equal(points[0][0],0.2);
drag(64,36,-50,500); assert.equal(points[0][0],0); assert.equal(points[0][1],1);
canvas.onpointerdown(event(0,180,"pointerdown"));
canvas.onpointermove(event(100,90,"pointermove",2)); assert.equal(points[0][0],0);
context.workflowBusy=true;
canvas.onpointermove(event(100,90,"pointermove")); assert.equal(points[0][0],0); assert.equal(captures.size,0);
context.workflowBusy=false;
canvas.onpointerdown(event(100,90,"pointerdown")); click(100,90); assert.equal(points.length,4);
click(100,90); assert.equal(points.length,4);
console.log("PASS: ROI drag, scaled coordinates, capture, bounds, duplicate, cancellation and busy guard.");
