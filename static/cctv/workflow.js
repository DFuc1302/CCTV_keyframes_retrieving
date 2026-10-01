"use strict";
let workflowData = null, workflowCatalog = [], workflowBusy = false, workflowUploading = false;
let workflowRois = {}, workflowImage = null, workflowFrameToken = 0, workflowShown = null;
const workflowCanvas = $("workflow-canvas"), workflowContext = workflowCanvas.getContext("2d");
function workflowProblem(message) { $("workflow-error").textContent = message; $("workflow-error").hidden = !message; }
async function workflowApi(url, payload) {
  const response = await fetch(url, payload === undefined ? undefined : {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Dữ liệu chưa hợp lệ. Kiểm tra lại lựa chọn.");
  return data;
}
async function workflowAction(action) {
  workflowProblem("");
  try { await action(); await workflowRefresh(); }
  catch (problem) { workflowProblem(problem.message); }
}
function workflowCamera() {
  const vid = $("workflow-roi-video").value;
  return document.querySelector(`[data-workflow-video="${vid}"]`)?.value || "CAM_1";
}
function workflowDraw() {
  workflowContext.clearRect(0, 0, 640, 360);
  if (workflowImage) workflowContext.drawImage(workflowImage, 0, 0, 640, 360);
  const points = workflowRois[workflowCamera()] || [];
  if (points.length) {
    workflowContext.beginPath(); points.forEach(([x,y], i) => i ? workflowContext.lineTo(x*640,y*360) : workflowContext.moveTo(x*640,y*360));
    if (points.length >= 3) { workflowContext.closePath(); workflowContext.fillStyle = "rgba(30,200,150,.25)"; workflowContext.fill(); }
    workflowContext.strokeStyle = "#00ffbb"; workflowContext.lineWidth = 3; workflowContext.stroke();
    points.forEach(([x,y]) => { workflowContext.beginPath(); workflowContext.arc(x*640,y*360,5,0,2*Math.PI); workflowContext.fillStyle="#fff"; workflowContext.fill(); });
  }
  $("workflow-roi-status").textContent = `${workflowCamera()} · ${points.length} đỉnh · Giữ và kéo đỉnh để chỉnh · Vùng tự đóng · ROI dùng chạy thử, chưa phê duyệt độc lập`;
}
async function workflowFrame() {
  if (!workflowData || !$("workflow-roi-video").value) return;
  const token = ++workflowFrameToken;
  const response = await fetch(`/cctv/workflow/${workflowData.id}/frame/${$("workflow-roi-video").value}?second=${Number($("workflow-start").value)}`);
  if (!response.ok) { const data = await response.json(); throw new Error(data.detail || "Không đọc được ảnh video."); }
  const url = URL.createObjectURL(await response.blob());
  try {
    const image = new Image(); image.src = url; await image.decode();
    if (token === workflowFrameToken) { workflowImage = image; workflowDraw(); }
  } finally { URL.revokeObjectURL(url); }
}
function workflowSelect(data) {
  workflowData = data;
  workflowRois = {};
  for (const camera of data.preparation?.roi.cameras || []) workflowRois[camera.camera_id] = camera.polygon || [];
  $("workflow-start").value = data.preparation?.start_sec ?? (data.demo ? 30 : 0);
  $("workflow-duration").value = data.preparation?.duration_sec ?? Math.min(10, ...data.videos.map(v => v.duration-v.first_pts));
  $("workflow-synthetic").checked = data.preparation?.synthetic_case ?? true;
  $("workflow-videos").replaceChildren(); $("workflow-roi-video").replaceChildren();
  data.videos.forEach(video => {
    const label = element("label", "workflow-video", `${video.name} · ${(video.duration-video.first_pts).toFixed(1)} giây · Camera`);
    const input = element("input"); input.value = video.camera_id; input.maxLength=64; input.dataset.workflowVideo=video.video_id;
    input.addEventListener("input", workflowDraw); label.append(input); $("workflow-videos").append(label);
    const option = element("option", "", video.name); option.value=video.video_id; $("workflow-roi-video").append(option);
  });
  workflowFrame().catch(problem => workflowProblem(problem.message));
}
async function workflowRefresh() {
  const catalog = await workflowApi("/cctv/workflow");
  workflowCatalog = catalog.datasets; workflowBusy = catalog.busy;
  const selected = $("workflow-dataset").value || workflowData?.id || catalog.active;
  $("workflow-dataset").replaceChildren(element("option", "", "Chọn bộ dữ liệu"));
  for (const data of workflowCatalog) { const option=element("option","",`${data.name}${data.active ? " · đang xem" : ""}`); option.value=data.id; $("workflow-dataset").append(option); }
  $("workflow-dataset").value=selected || "";
  const data = workflowCatalog.find(d => d.id === selected);
  if (data && workflowData?.id !== data.id) workflowSelect(data);
  else if (data) workflowData = data;
  const job=data?.job;
  $("workflow-job").textContent = job ? `${job.stage} · ${job.completed}/${job.total} · ${job.status}${job.error ? " — " + job.error : ""}` : "Lưu chuẩn bị rồi bấm Chạy toàn bộ pipeline.";
  $("workflow-progress").max=job?.total || 1; $("workflow-progress").value=job?.completed || 0;
  const busy=workflowBusy || workflowUploading;
  for (const id of ["workflow-demo","workflow-new","workflow-save","workflow-frame","workflow-full","workflow-clear","workflow-undo","workflow-dataset","workflow-start","workflow-duration","workflow-roi-video","workflow-synthetic"]) $(id).disabled=busy;
  document.querySelectorAll("[data-workflow-video]").forEach(input=>input.disabled=busy);
  $("workflow-files").disabled=busy || !data || data.demo || data.videos.length>=6;
  $("workflow-run").disabled=busy || !data?.preparation || !catalog.models_ready;
  $("workflow-models").disabled=busy || !data || catalog.models_ready;
  $("workflow-retry").disabled=busy || !["failed","cancelled","interrupted"].includes(job?.status);
  $("workflow-stop").disabled=!workflowBusy;
  $("workflow-activate").disabled=busy || !data?.has_results;
  $("workflow-report").disabled=busy || !data?.has_results;
  const verification=data?.verification_job;
  $("workflow-verify").disabled=busy || !data?.has_results;
  $("workflow-verify-retry").disabled=busy || !["failed","cancelled","interrupted"].includes(verification?.status);
  const stepLabels={inventory:"Kiểm kê dữ liệu",roi:"Kiểm tra vùng đường",fixtures:"Kiểm tra phép tính tổng hợp","cpu-tests":"Kiểm tra quy tắc và ràng buộc",http:"Kiểm tra API và dữ liệu hiển thị"};
  $("workflow-verify-status").textContent=verification ? `${stepLabels[verification.stage] || verification.stage} · ${verification.completed}/${verification.total} · ${verification.status}${verification.error ? " — "+verification.error : ""}` : "Chạy pipeline trước để tự kiểm chứng demo. M8 và chất lượng độc lập vẫn pending.";
  $("workflow-verify-progress").value=verification?.completed || 0;
  $("workflow-verify-results").replaceChildren();
  if(verification?.report) {
    $("workflow-verify-results").append(element("li","",`Demo đạt · ${verification.report.verified_utc} · không đo accuracy CCTV`));
    const pendingLabels={T10_1:"Chờ review nội dung và xác minh nhóm video",T10_2:"Chờ người dùng duyệt ROI",T10_3:"Chưa chia calibration/test cho footage",T10_4:"M8 giữ pending",T10_5:"Chưa hiệu chỉnh ngưỡng bằng nhãn độc lập",T10_6:"Chưa đánh giá chất lượng footage",T10_7:"Chưa chạy máy thứ hai"};
    for(const [task,reason] of Object.entries(verification.report.pending)) $("workflow-verify-results").append(element("li","hint",`${task.replace("_",".")}: ${pendingLabels[task] || reason}`));
  }
  if (data && (job || verification)) { const log=await workflowApi(`/cctv/workflow/${data.id}/log${verification && !["queued","running"].includes(job?.status) ? "?verification=true" : ""}`); $("workflow-log").textContent=log.text; }
  if (job?.status === "complete" && data.active && workflowShown !== data.id+job.id) { workflowShown=data.id+job.id; await checkReady(); await refreshTraffic(); await refreshEvents(); await refreshDispatch(); }
}
$("workflow-demo").onclick=()=>workflowAction(async()=>{ const data=await workflowApi("/cctv/workflow/datasets",{name:$("workflow-name").value,demo:true}); workflowData=null; $("workflow-dataset").value=""; workflowSelect(data); });
$("workflow-new").onclick=()=>workflowAction(async()=>{ const data=await workflowApi("/cctv/workflow/datasets",{name:$("workflow-name").value,demo:false}); workflowData=null; $("workflow-dataset").value=""; workflowSelect(data); });
$("workflow-dataset").onchange=()=>{ const data=workflowCatalog.find(d=>d.id===$("workflow-dataset").value); if(data) workflowSelect(data); workflowRefresh().catch(problem=>workflowProblem(problem.message)); };
$("workflow-files").onchange=()=>workflowAction(async()=>{
  const files=Array.from($("workflow-files").files);
  if (!workflowData || files.length+workflowData.videos.length>6 || files.some(file=>file.size>1024**3)) throw new Error("Tối đa sáu video, mỗi video không quá 1 GiB.");
  workflowUploading=true; $("workflow-upload").hidden=false; await workflowRefresh();
  try {
    for (const file of files) {
      await new Promise((resolve,reject)=>{
        const xhr=new XMLHttpRequest(); xhr.open("POST",`/cctv/workflow/${workflowData.id}/upload?filename=${encodeURIComponent(file.name)}`);
        xhr.setRequestHeader("Content-Type","application/octet-stream");
        xhr.upload.onprogress=event=>{ if(event.lengthComputable) $("workflow-upload").value=event.loaded/event.total*100; };
        xhr.onerror=()=>reject(new Error("Upload gián đoạn; chọn lại video đó."));
        xhr.onload=()=>{ try { const data=JSON.parse(xhr.responseText); if(xhr.status>=400) reject(new Error(data.detail)); else { workflowSelect(data); resolve(); } } catch { reject(new Error("Upload chưa hoàn tất; kiểm tra kết nối rồi thử lại.")); } };
        xhr.send(file);
      });
    }
  } finally { workflowUploading=false; $("workflow-upload").hidden=true; $("workflow-files").value=""; }
});
$("workflow-frame").onclick=()=>workflowAction(workflowFrame);
$("workflow-roi-video").onchange=()=>workflowAction(workflowFrame);
let workflowDrag=null, workflowSkipClick=false;
function workflowPoint(event) {
  const rect=workflowCanvas.getBoundingClientRect();
  return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];
}
function workflowNear(points, point, radius, excluded=-1) {
  const rect=workflowCanvas.getBoundingClientRect();
  return points.findIndex(([x,y],i)=>i!==excluded && Math.hypot((x-point[0])*rect.width,(y-point[1])*rect.height)<radius);
}
function workflowEndDrag(event) {
  if(!workflowDrag || event.pointerId!==workflowDrag.pointerId) return;
  const drag=workflowDrag; workflowDrag=null;
  if(event.type!=="pointerup" || workflowBusy || workflowUploading || workflowNear(drag.points,drag.points[drag.index],6,drag.index)>=0) {
    drag.points[drag.index]=drag.original;
    if(event.type==="pointerup" && !workflowBusy && !workflowUploading) workflowProblem("Không đặt hai đỉnh trùng nhau. Đỉnh đã trở về vị trí cũ.");
  }
  workflowSkipClick=true; workflowCanvas.style.cursor="crosshair";
  if(workflowCanvas.hasPointerCapture(event.pointerId)) workflowCanvas.releasePointerCapture(event.pointerId);
  workflowDraw();
}
workflowCanvas.onpointerdown=event=>{
  if(workflowDrag || workflowBusy || workflowUploading || event.button!==0) return;
  workflowSkipClick=false;
  const points=workflowRois[workflowCamera()] || [], index=workflowNear(points,workflowPoint(event),10);
  if(index<0) return;
  workflowDrag={points,index,original:[...points[index]],pointerId:event.pointerId};
  workflowProblem(""); workflowCanvas.setPointerCapture(event.pointerId); workflowCanvas.style.cursor="grabbing";
  event.preventDefault();
};
workflowCanvas.onpointermove=event=>{
  if(workflowDrag && event.pointerId===workflowDrag.pointerId) {
    if(workflowBusy || workflowUploading) { workflowEndDrag(event); return; }
    workflowDrag.points[workflowDrag.index]=workflowPoint(event); workflowDraw();
  } else if(!workflowDrag) workflowCanvas.style.cursor=workflowNear(workflowRois[workflowCamera()] || [],workflowPoint(event),10)>=0 ? "grab" : "crosshair";
};
workflowCanvas.onpointerup=workflowCanvas.onpointercancel=workflowCanvas.onlostpointercapture=workflowEndDrag;
workflowCanvas.onclick=event=>{
  if(workflowSkipClick) { workflowSkipClick=false; return; }
  if(workflowBusy || workflowUploading || event.detail>1) return;
  const rect=workflowCanvas.getBoundingClientRect(), x=(event.clientX-rect.left)/rect.width, y=(event.clientY-rect.top)/rect.height;
  const points=workflowRois[workflowCamera()] ||= [];
  if(x<0 || x>1 || y<0 || y>1) return;
  if(points.some(([px,py])=>Math.hypot((px-x)*rect.width,(py-y)*rect.height)<6)) { workflowProblem("Đỉnh này đã có. Vùng tự đóng; không cần click lại điểm đầu."); return; }
  if(points.length>=20) { workflowProblem("Tối đa 20 đỉnh trên UI. Dùng Bỏ đỉnh cuối hoặc Vẽ lại để sửa."); return; }
  workflowProblem(""); points.push([x,y]); workflowDraw();
};
$("workflow-undo").onclick=()=>{ workflowRois[workflowCamera()]?.pop(); workflowDraw(); };
$("workflow-clear").onclick=()=>{ workflowRois[workflowCamera()]=[]; workflowDraw(); };
$("workflow-full").onclick=()=>{ workflowRois[workflowCamera()]=[[0,0],[1,0],[1,1],[0,1]]; workflowDraw(); };
$("workflow-save").onclick=()=>workflowAction(async()=>{
  if(!workflowData) throw new Error("Chọn bộ dữ liệu trước.");
  const cameras=Object.fromEntries(Array.from(document.querySelectorAll("[data-workflow-video]")).map(input=>[input.dataset.workflowVideo,input.value.trim()]));
  await workflowApi(`/cctv/workflow/${workflowData.id}/prepare`,{cameras,rois:workflowRois,start_sec:Number($("workflow-start").value),duration_sec:Number($("workflow-duration").value),synthetic_case:$("workflow-synthetic").checked});
});
for(const [id,payload] of [["workflow-run",{}],["workflow-models",{download:true}],["workflow-retry",{retry:true}]]) $(id).onclick=()=>workflowAction(async()=>{ await workflowApi(`/cctv/workflow/${workflowData.id}/jobs`,payload); await checkReady(); });
$("workflow-stop").onclick=()=>workflowAction(()=>workflowApi("/cctv/workflow/stop",{}));
$("workflow-activate").onclick=()=>workflowAction(async()=>{ ready=false; setBusy(false); $("status").textContent="Đang mở bộ dữ liệu…"; try { await workflowApi(`/cctv/workflow/${workflowData.id}/activate`,{}); await refreshTraffic(); await refreshEvents(); await refreshDispatch(); selectSection("search"); $("workflow-panel").open=false; } finally { await checkReady(); } });
$("workflow-report").onclick=()=>{ location.href=`/cctv/workflow/${workflowData.id}/report`; };
for(const [id,retry] of [["workflow-verify",false],["workflow-verify-retry",true]]) $(id).onclick=()=>workflowAction(async()=>{ await workflowApi(`/cctv/workflow/${workflowData.id}/verify`,{retry}); });
setInterval(()=>workflowRefresh().catch(()=>{}),1500);
workflowRefresh().catch(problem=>workflowProblem(problem.message));
