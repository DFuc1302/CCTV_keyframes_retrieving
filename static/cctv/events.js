"use strict";
const eventPackageLabels = {incidents: "Rule nghi sự cố · footage", congestion: "Rule tắc đường · footage", demo: "Case tổng hợp · chỉ chạy thử"};
const eventTypeLabels = {demo_case: "Case tổng hợp", congestion_candidate: "Ứng viên tắc đường", suspected_road_incident: "Nghi sự cố · cần đối chiếu"};
const eventStatusLabels = {pending_review: "Chưa review", needs_more_evidence: "Cần thêm bằng chứng", confirmed: "Đã review confirmed", rejected: "Đã review rejected"};
let eventRequest = 0, eventOffset = 0, eventTotal = 0;
const eventPageSize = 20;
function eventProblem(message) {
  $("events-error").textContent = message; $("events-error").hidden = !message;
  $("events-list").replaceChildren(); $("events-empty").hidden = true;
  $("events-prev").disabled = true; $("events-next").disabled = true;
  $("events-page").textContent = "";
}
async function eventFetch(url) {
  const response = await fetch(url); const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Sự kiện chưa khả dụng.");
  return data;
}
function renderEvent(row) {
  const card = element("article", "event-card");
  card.append(element("h3", "", eventTypeLabels[row.event_type] || row.event_type));
  card.append(element("p", "event-meta", `${row.camera_id} · ${row.video_id} · ${timeLabel(row.start_sec)} – ${timeLabel(row.end_sec)}`));
  card.append(element("p", "signal-label", `${eventStatusLabels[row.status] || row.status} · revision ${row.revision}`));
  card.append(element("p", "demo-notice", row.synthetic_event ? "Tình huống tổng hợp trên evidence footage; không phải sự cố được phát hiện." : "Ứng viên từ rule; ngưỡng chưa hiệu chỉnh, chưa kết luận tai nạn/tắc đường."));
  card.append(element("p", "event-rationale", row.rationale));
  if (row.review) {
    card.append(element("p", "hint", `Review: ${row.review.reviewer} · ${row.review.reviewed_at} · ${row.review.scope}`));
    card.append(element("p", "event-rationale", row.review.reason));
  }
  const links = element("div", "event-evidence");
  row.evidence.forEach((loc, index) => {
    const button = element("button", "outline-button", `Evidence ${index + 1} · ${timeLabel(loc.timestamp_sec)} · frame ${loc.frame_idx} ↗`);
    button.type = "button"; button.addEventListener("click", () => view(loc)); links.append(button);
  });
  card.append(links, element("p", "hint", row.event_id));
  if (typeof workflowData !== "undefined" && workflowData?.active) {
    const form=element("div","review-demo-form"), message=element("p","hint");
    form.append(element("p","demo-notice","Review demo một người — không tạo nhãn độc lập M8."));
    const reviewer=element("input"), reason=element("input"), decision=element("select");
    reviewer.maxLength=100; reason.maxLength=1000;
    for(const [input,labelText,placeholder] of [[reviewer,"Tên người review","Tên của bạn"],[reason,"Lý do quyết định","Bằng chứng quan sát được hoặc lý do chưa chắc"]]) { const label=element("label","",labelText); input.placeholder=placeholder; label.append(input); form.append(label); }
    for(const [value,label] of [["needs_more_evidence","Cần thêm bằng chứng"],["confirmed","Xác nhận trong demo"],["rejected","Bác bỏ trong demo"]]) { const option=element("option","",label); option.value=value; decision.append(option); }
    const decisionLabel=element("label","","Quyết định demo"); decisionLabel.append(decision); form.append(decisionLabel);
    const review=element("button","outline-button","Lưu review demo"); review.type="button";
    const officers=element("input"), priority=element("input"), skill=element("select");
    officers.type="number"; officers.min=1; officers.max=240; officers.value=2;
    priority.type="number"; priority.min=1; priority.max=5; priority.value=4;
    for(const [input,labelText] of [[officers,"Số người cần (nguồn lực mô phỏng)"],[priority,"Ưu tiên 1–5"]]) { const label=element("label","",labelText); label.append(input); form.append(label); }
    for(const [value,label] of [["scene_assessment","Đánh giá hiện trường"],["traffic_control","Điều tiết giao thông"]]) { const option=element("option","",label); option.value=value; skill.append(option); }
    const skillLabel=element("label","","Kỹ năng cần"); skillLabel.append(skill); form.append(skillLabel);
    const propose=element("button","outline-button","Tính phân bổ mô phỏng"); propose.type="button";
    const packageId=$("events-package").value;
    review.onclick=async()=>{ review.disabled=true; message.textContent="Đang lưu…"; try { if(!reviewer.value.trim() || !reason.value.trim()) throw new Error("Nhập tên và lý do trước khi lưu."); await workflowApi(`/cctv/workflow/review/${packageId}`,{event_id:row.event_id,decision:decision.value,reviewer:reviewer.value.trim(),reason:reason.value.trim(),expected_revision:row.revision}); await loadEvents(); await refreshDispatch(); } catch(problem) { message.textContent=problem.message; } finally {review.disabled=false;} };
    propose.onclick=async()=>{ propose.disabled=true; message.textContent="Đang tính…"; try { const result=await workflowApi(`/cctv/workflow/proposal/${packageId}`,{event_id:row.event_id,min_officers:Number(officers.value),priority:Number(priority.value),required_skills:[skill.value]}); message.textContent=`${result.assignments.length} assignment, ${result.unmet_demands.length} chưa đáp ứng. Xem tab Phân bổ mô phỏng.`; await refreshDispatch(); } catch(problem) { message.textContent=problem.message; } finally {propose.disabled=false;} };
    form.append(review,propose,message); card.append(form);
  }
  return card;
}
async function loadEvents() {
  const token = ++eventRequest, packageId = $("events-package").value;
  eventProblem(""); $("events-list").setAttribute("aria-busy", "true");
  $("events-status").textContent = "Đang đọc sự kiện và review…";
  try {
    if (!packageId) throw new Error("Chưa có gói sự kiện khả dụng.");
    const data = await eventFetch(`/cctv/events/${encodeURIComponent(packageId)}?${new URLSearchParams({offset: eventOffset, limit: eventPageSize})}`);
    if (token !== eventRequest) return;
    eventTotal = data.total;
    data.rows.forEach(row => $("events-list").append(renderEvent(row)));
    $("events-status").textContent = `${data.total} sự kiện · ${eventPackageLabels[packageId] || packageId} · ${typeof workflowData !== "undefined" && workflowData?.active ? "review demo" : "chỉ đọc"}`;
    $("events-empty").hidden = data.total > 0;
    $("events-empty").textContent = "Gói đã được kiểm tra và hiện không có ứng viên. Kết quả này chưa chứng minh footage không có sự cố.";
    $("events-page").textContent = data.total ? `${eventOffset + 1}–${eventOffset + data.rows.length} / ${data.total}` : "0 sự kiện";
    $("events-prev").disabled = eventOffset === 0;
    $("events-next").disabled = eventOffset + eventPageSize >= eventTotal;
  } catch (problem) {
    if (token !== eventRequest) return;
    eventProblem(problem.message || "Không đọc được sự kiện."); $("events-status").textContent = "Gói sự kiện chưa khả dụng.";
  } finally { if (token === eventRequest) $("events-list").setAttribute("aria-busy", "false"); }
}
async function refreshEvents() {
  const token = ++eventRequest;
  $("events-refresh").disabled = true; eventProblem("");
  try {
    const data = await eventFetch("/cctv/events");
    if (token !== eventRequest) return;
    const previous = $("events-package").value;
    $("events-package").replaceChildren();
    data.packages.forEach(row => {
      const option = element("option", "", `${eventPackageLabels[row.package_id] || row.package_id}${row.available ? ` (${row.total})` : " · chưa khả dụng"}`);
      option.value = row.package_id; option.disabled = !row.available; $("events-package").append(option);
    });
    const available = data.packages.filter(p => p.available);
    if (!available.length) throw new Error("Chưa có gói sự kiện khả dụng; retrieval vẫn dùng được.");
    $("events-package").value = available.some(p => p.package_id === previous) ? previous : available[0].package_id;
    eventOffset = 0; await loadEvents();
  } catch (problem) { if (token === eventRequest) { eventProblem(problem.message); $("events-status").textContent = "Sự kiện chưa khả dụng."; } }
  finally { $("events-refresh").disabled = false; }
}
$("events-package").addEventListener("change", () => { eventOffset = 0; loadEvents(); });
$("events-prev").addEventListener("click", () => { eventOffset = Math.max(0, eventOffset - eventPageSize); loadEvents(); });
$("events-next").addEventListener("click", () => { eventOffset += eventPageSize; loadEvents(); });
$("events-refresh").addEventListener("click", refreshEvents);
refreshEvents();
