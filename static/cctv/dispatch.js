"use strict";
let dispatchRequest = 0;
const dispatchLabels = {simulation: "Nhu cầu tổng hợp", events_empty: "Gói sự kiện chưa có yêu cầu", review_gate: "Case demo kiểm tra review"};
const dispatchReasons = {
  review_not_confirmed: "Review chưa xác nhận; chưa đủ điều kiện phân bổ",
  shared_staff_cap_or_competing_demand: "Giới hạn nhân lực chung hoặc đội đã xếp cho yêu cầu ưu tiên hơn",
  camera_mapping_unknown: "Chưa xác định khu vực camera",
  insufficient_eligible_staff: "Không đủ nhân lực đáp ứng điều kiện",
  dispatch_cap_insufficient: "Giới hạn nhân lực không đủ cho yêu cầu",
  unit_not_available: "Đội không sẵn sàng",
  required_skill_missing: "Thiếu kỹ năng yêu cầu",
  zone_not_allowed: "Ngoài khu vực được phép",
  travel_time_missing: "Thiếu thời gian di chuyển",
  travel_deadline_exceeded: "Vượt giới hạn thời gian di chuyển"
};
function dispatchProblem(message) {
  $("dispatch-error").textContent = message; $("dispatch-error").hidden = !message;
  $("dispatch-data").hidden = true;
  $("dispatch-assignments").replaceChildren(); $("dispatch-unmet").replaceChildren();
}
function dispatchCard(row, assigned) {
  const card = element("article", "event-card");
  card.append(element("h4", "", row.demand_id));
  card.append(element("p", "", `${row.camera_id} · ${row.zone_id || "Khu vực chưa biết"} · ưu tiên ${row.priority} · cần ${row.min_officers} người`));
  card.append(element("p", "demo-notice", row.synthetic_demand ? "Nhu cầu/tình huống tổng hợp để kiểm thử." : "Nhu cầu từ ứng viên sự kiện; nguồn lực vẫn mô phỏng."));
  card.append(element("p", "signal-label", `Review: ${eventStatusLabels[row.review_status] || row.review_status}${row.synthetic_demand && row.review_status === "confirmed" ? " (fixture)" : ""}`));
  if (assigned) {
    card.append(element("p", "", `${row.units.join(", ")} · ${row.officers} người · tổng di chuyển giả định ${row.total_simulated_travel_sec} giây`));
    card.append(element("p", "hint", "Đội đáp ứng kỹ năng, khu vực, thời gian và ngân sách/dự phòng trong báo cáo mô phỏng."));
  } else {
    row.reasons.forEach(reason => card.append(element("p", "", dispatchReasons[reason] || reason)));
    const detail = element("details", ""); detail.append(element("summary", "", "Điều kiện từng đội"));
    Object.entries(row.unit_reasons).forEach(([unit, reasons]) => detail.append(element("p", "hint", `${unit}: ${reasons.length ? reasons.map(r => dispatchReasons[r] || r).join("; ") : "Đáp ứng điều kiện riêng; còn phụ thuộc giới hạn chung"}`)));
    card.append(detail);
  }
  if (row.event_id) card.append(element("p", "hint", row.event_id));
  return card;
}
async function loadDispatch() {
  const token = ++dispatchRequest, packageId = $("dispatch-package").value;
  dispatchProblem(""); $("dispatch-status").textContent = "Đang kiểm tra đầu vào báo cáo…";
  try {
    if (!packageId) throw new Error("Chưa có báo cáo phân bổ khả dụng.");
    const data = await eventFetch(`/cctv/dispatch/${encodeURIComponent(packageId)}`);
    if (token !== dispatchRequest) return;
    $("dispatch-metrics").replaceChildren();
    [["Nhân lực trong roster", data.roster_officers], ["Đang sẵn sàng", data.available_officers], ["Giới hạn được xếp", data.dispatchable_officer_limit], ["Đã xếp trong mô phỏng", data.assigned_officers], ["Sẵn sàng còn lại", data.available_officers_remaining], ["Dự phòng bắt buộc", data.required_reserve_officers]].forEach(([label, number]) => {
      const item = element("div", ""); item.append(element("span", "", label), element("strong", "", String(number)), element("small", "", "người · giả định")); $("dispatch-metrics").append(item);
    });
    data.assignments.forEach(row => $("dispatch-assignments").append(dispatchCard(row, true)));
    data.unmet_demands.forEach(row => $("dispatch-unmet").append(dispatchCard(row, false)));
    if (!data.assignments.length) $("dispatch-assignments").append(element("p", "hint", "Không có đề xuất xếp đội trong báo cáo này."));
    if (!data.unmet_demands.length) $("dispatch-unmet").append(element("p", "hint", "Không có yêu cầu chưa phân bổ trong báo cáo này."));
    $("dispatch-summary").textContent = `${data.reserve_met ? "Đủ" : "Thiếu"} dự phòng · ${data.assignments.length} yêu cầu đã xếp · ${data.unmet_demands.length} yêu cầu chưa xếp. Review và cấu hình đã được đối chiếu tại lần đọc này.`;
    $("dispatch-snapshot").textContent = `Snapshot SHA-256: ${data.snapshot_sha256}`;
    $("dispatch-status").textContent = `${dispatchLabels[packageId] || packageId} · chỉ đọc · mô phỏng`;
    $("dispatch-data").hidden = false;
  } catch (problem) { if (token === dispatchRequest) { dispatchProblem(problem.message); $("dispatch-status").textContent = "Báo cáo phân bổ chưa khả dụng."; } }
}
async function refreshDispatch() {
  const token = ++dispatchRequest; $("dispatch-refresh").disabled = true; dispatchProblem("");
  try {
    const data = await eventFetch("/cctv/dispatch"); if (token !== dispatchRequest) return;
    const previous = $("dispatch-package").value; $("dispatch-package").replaceChildren();
    data.packages.forEach(row => {
      const option = element("option", "", `${dispatchLabels[row.package_id] || row.package_id}${row.available ? "" : " · chưa khả dụng"}`);
      option.value = row.package_id; option.disabled = !row.available; $("dispatch-package").append(option);
    });
    const available = data.packages.filter(row => row.available);
    if (!available.length) throw new Error("Báo cáo thiếu hoặc đầu vào đã đổi. Retrieval vẫn dùng được.");
    $("dispatch-package").value = available.some(row => row.package_id === previous) ? previous : available[0].package_id;
    await loadDispatch();
  } catch (problem) { if (token === dispatchRequest) { dispatchProblem(problem.message); $("dispatch-status").textContent = "Phân bổ chưa khả dụng."; } }
  finally { $("dispatch-refresh").disabled = false; }
}
$("dispatch-package").addEventListener("change", loadDispatch);
$("dispatch-refresh").addEventListener("click", refreshDispatch);
refreshDispatch();
