"use strict";
let trafficCatalog = [], trafficSelected = null, trafficLoading = false;
let trafficOffset = 0, trafficRequest = 0;
const trafficPageSize = 40;
const trafficLabels = {
  insufficient_evidence: "Thiếu bằng chứng",
  no_candidate: "Không tạo ứng viên",
  slow_dense: "Đông và chậm — đang theo dõi",
  congestion_candidate: "Ứng viên tắc đường — cần xem lại"
};
function trafficNumber(value) {
  return value === null ? "Chưa đủ dữ liệu" : Number(value).toLocaleString("vi-VN", {maximumFractionDigits: 1});
}
function trafficTime(row) { return `${timeLabel(row.window.start_sec)} – ${timeLabel(row.window.end_sec)}`; }
function selectSection(key) {
  for (const name of ["search", "traffic"]) {
    const active = name === key;
    $(name + "-section-tab").classList.toggle("active", active);
    $(name + "-section-tab").setAttribute("aria-selected", String(active));
    $(name + "-section-tab").tabIndex = active ? 0 : -1;
    $(name + "-workspace").hidden = !active;
  }
}
for (const name of ["search", "traffic"]) {
  $(name + "-section-tab").addEventListener("click", () => selectSection(name));
  $(name + "-section-tab").addEventListener("keydown", event => {
    if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault(); const next = name === "search" ? "traffic" : "search";
      selectSection(next); $(next + "-section-tab").focus();
    }
  });
}
function trafficProblem(message) {
  $("traffic-error").textContent = message; $("traffic-error").hidden = !message;
  $("traffic-data").hidden = true; trafficSelected = null;
  $("traffic-view").disabled = true;
}
async function trafficFetch(url) {
  const response = await fetch(url); const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Thống kê chưa khả dụng.");
  return data;
}
async function refreshTraffic() {
  const token = ++trafficRequest;
  trafficProblem(""); $("traffic-status").textContent = "Đang kiểm tra thống kê…";
  $("traffic-refresh").disabled = true;
  try {
    const data = await trafficFetch("/cctv/traffic");
    if (token !== trafficRequest) return;
    if (!data.available) throw new Error(data.message);
    trafficCatalog = data.videos;
    $("traffic-notice").textContent = data.message + " Chưa phải kết luận tắc đường.";
    $("traffic-status").textContent = `${data.windows} cửa sổ · ${data.videos.length} video · ${data.demo_only ? "Chế độ ROI nháp" : "ROI đã duyệt"}`;
    $("traffic-camera").replaceChildren();
    const all = element("option", "", "Tất cả camera"); all.value = ""; $("traffic-camera").append(all);
    [...new Set(data.videos.map(row => row.camera_id))].sort().forEach(camera => {
      const option = element("option", "", camera); option.value = camera; $("traffic-camera").append(option);
    });
    updateTrafficVideos();
  } catch (problem) {
    if (token === trafficRequest) { $("traffic-status").textContent = "Thống kê chưa khả dụng"; trafficProblem(problem.message || "Không kết nối được server."); }
  } finally { $("traffic-refresh").disabled = false; }
}
function updateTrafficVideos() {
  const camera = $("traffic-camera").value;
  $("traffic-video").replaceChildren();
  trafficCatalog.filter(row => !camera || row.camera_id === camera).forEach(row => {
    const option = element("option", "", `${row.video_id} · ${row.camera_id}`); option.value = row.video_id; $("traffic-video").append(option);
  });
  loadTrafficVideo(0);
}
function selectTrafficWindow(row, index) {
  if (trafficLoading) return;
  trafficSelected = row;
  $("traffic-selected").textContent = trafficTime(row);
  $("metric-count").textContent = trafficNumber(row.window.mean_vehicle_count);
  $("metric-occupancy").textContent = `${trafficNumber(row.window.mean_occupancy * 100)}%`;
  $("metric-motion").textContent = trafficNumber(row.window.median_motion_px_per_sec);
  $("metric-coverage").textContent = `${trafficNumber(row.coverage * 100)}%`;
  $("traffic-signal").textContent = trafficLabels[row.status];
  $("traffic-signal").classList.toggle("candidate", row.status === "congestion_candidate");
  $("traffic-evidence").textContent = `${row.motion_pairs} cặp motion · Điều kiện duy trì ${trafficNumber(row.persistent_sec)} giây. Ngưỡng chưa hiệu chỉnh.`;
  $("traffic-view").disabled = false;
  Array.from($("traffic-bars").children).forEach((button, i) => { button.classList.toggle("selected", i === index); button.setAttribute("aria-pressed", String(i === index)); });
}
function renderTrafficRows(rows) {
  $("traffic-bars").replaceChildren(); $("traffic-table-body").replaceChildren();
  $("traffic-bars").style.gridTemplateColumns = `repeat(${Math.max(1, rows.length)}, minmax(0, 1fr))`;
  rows.forEach((row, index) => {
    const bar = element("button", "traffic-bar"); bar.type = "button";
    bar.setAttribute("aria-label", `${trafficTime(row)}, occupancy ${trafficNumber(row.window.mean_occupancy * 100)}%, ${trafficLabels[row.status]}`);
    bar.title = bar.getAttribute("aria-label");
    const fill = element("span", "bar-fill"); fill.style.height = `${Math.max(2, row.window.mean_occupancy * 100)}%`;
    bar.append(fill, element("span", "bar-time", timeLabel(row.window.start_sec).slice(0, 5)));
    bar.addEventListener("click", () => selectTrafficWindow(row, index)); $("traffic-bars").append(bar);
    const tr = element("tr"), timeCell = element("td"), button = element("button", "window-link", trafficTime(row));
    button.type = "button"; button.addEventListener("click", () => { if (trafficLoading) return; selectTrafficWindow(row, index); view(row.evidence); });
    timeCell.append(button); tr.append(timeCell);
    [trafficNumber(row.window.mean_vehicle_count), `${trafficNumber(row.window.mean_occupancy * 100)}%`,
      trafficNumber(row.window.median_motion_px_per_sec), `${trafficNumber(row.coverage * 100)}%`, trafficLabels[row.status]]
      .forEach(text => tr.append(element("td", "", text)));
    $("traffic-table-body").append(tr);
  });
  if (rows.length) selectTrafficWindow(rows[0], 0);
}
async function loadTrafficVideo(offset) {
  const token = ++trafficRequest, vid = $("traffic-video").value;
  trafficLoading = true;
  trafficSelected = null; $("traffic-view").disabled = true;
  $("traffic-prev").disabled = true; $("traffic-next").disabled = true;
  $("traffic-data").setAttribute("aria-busy", "true"); $("traffic-error").hidden = true;
  try {
    const data = await trafficFetch(`/cctv/traffic/windows/${encodeURIComponent(vid)}?${new URLSearchParams({offset, limit: trafficPageSize})}`);
    if (token !== trafficRequest) return;
    trafficOffset = offset; $("traffic-data").hidden = false;
    $("traffic-range").textContent = `${timeLabel(data.video.start_sec)} – ${timeLabel(data.video.end_sec)} · ${data.total} cửa sổ đã xử lý`;
    $("traffic-roi").src = data.video.roi_image_url;
    $("traffic-roi").alt = `ROI ${data.demo_only ? "nháp" : "đã duyệt"} của camera ${data.video.camera_id}`;
    $("traffic-roi-caption").textContent = `${data.video.video_id} · Ảnh ROI tại ${timeLabel(data.video.roi_evidence.timestamp_sec)} · ${data.demo_only ? "Chưa duyệt" : "Đã duyệt"}`;
    trafficLoading = false; renderTrafficRows(data.rows);
    $("traffic-page").textContent = data.rows.length ? `Cửa sổ ${offset + 1}–${offset + data.rows.length} / ${data.total}` : "Không có cửa sổ trong trang này.";
    $("traffic-prev").disabled = offset === 0; $("traffic-next").disabled = offset + data.rows.length >= data.total;
  } catch (problem) { if (token === trafficRequest) trafficProblem(problem.message || "Không tải được thống kê."); }
  finally { if (token === trafficRequest) { trafficLoading = false; $("traffic-data").setAttribute("aria-busy", "false"); } }
}
$("traffic-camera").addEventListener("change", updateTrafficVideos);
$("traffic-video").addEventListener("change", () => loadTrafficVideo(0));
$("traffic-refresh").addEventListener("click", refreshTraffic);
$("traffic-prev").addEventListener("click", () => loadTrafficVideo(Math.max(0, trafficOffset - trafficPageSize)));
$("traffic-next").addEventListener("click", () => loadTrafficVideo(trafficOffset + trafficPageSize));
$("traffic-view").addEventListener("click", () => { if (trafficSelected && !trafficLoading) view(trafficSelected.evidence); });
$("traffic-roi").addEventListener("error", () => trafficProblem("Ảnh ROI chưa khả dụng. Kiểm tra lại trạng thái giao thông."));
refreshTraffic();
