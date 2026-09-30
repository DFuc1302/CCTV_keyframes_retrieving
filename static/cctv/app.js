"use strict";
const $ = id => document.getElementById(id);
let mode = "text", ready = false, busy = false, objectUrl = null;
function timeLabel(seconds) {
  const milliseconds = Math.round(seconds * 1000);
  const minutes = Math.floor(milliseconds / 60000);
  return `${String(minutes).padStart(2, "0")}:${String(Math.floor(milliseconds / 1000) % 60).padStart(2, "0")}.${String(milliseconds % 1000).padStart(3, "0")}`;
}
function setBusy(value) {
  busy = value;
  $("submit").disabled = value || !ready;
  $("submit").textContent = value ? "Đang tìm kiếm…" : "Tìm keyframes →";
  $("results").setAttribute("aria-busy", String(value));
  document.querySelectorAll("[data-query]").forEach(button => { button.disabled = value; });
}
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
function selectMode(next) {
  mode = next;
  for (const key of ["text", "image"]) {
    $(key + "-tab").classList.toggle("active", next === key);
    $(key + "-tab").setAttribute("aria-selected", String(next === key));
    $(key + "-tab").tabIndex = next === key ? 0 : -1;
    $(key + "-panel").hidden = next !== key;
  }
}
for (const key of ["text", "image"]) {
  $(key + "-tab").addEventListener("click", () => selectMode(key));
  $(key + "-tab").addEventListener("keydown", event => {
    if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
      event.preventDefault();
      const next = key === "text" ? "image" : "text";
      selectMode(next); $(next + "-tab").focus();
    }
  });
}
async function checkReady() {
  try {
    const response = await fetch("/ready");
    const data = await response.json();
    ready = response.ok && data.ready === true;
    $("status").textContent = ready ? "● Dữ liệu sẵn sàng" : "Dữ liệu chưa sẵn sàng";
    $("status").classList.toggle("ready", ready);
    $("dataset-count").textContent = ready ? `${data.frames.toLocaleString("vi-VN")} keyframes · ${data.cameras.length} camera` : "Thử kiểm tra lại trạng thái sau.";
  } catch (_) {
    ready = false; $("status").textContent = "Không kết nối được server";
  }
  setBusy(busy);
}
$("refresh").addEventListener("click", checkReady);
$("image-file").addEventListener("change", () => {
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  const file = $("image-file").files[0];
  $("query-image").hidden = !file;
  if (file) { objectUrl = URL.createObjectURL(file); $("query-image").src = objectUrl; }
});
function element(tag, className, text) {
  const node = document.createElement(tag); if (className) node.className = className;
  if (text !== undefined) node.textContent = text; return node;
}
function view(result) {
  $("viewer-title").textContent = `${result.video_id} · Camera ${result.camera_id}`;
  $("viewer-meta").textContent = `${timeLabel(result.timestamp_sec)} trong video · Frame nguồn ${result.frame_idx}`;
  $("video-error").hidden = true;
  const video = $("video");
  video.onloadedmetadata = () => {
    video.currentTime = Math.min(Math.max(0, result.video_seek_sec), Math.max(0, video.duration - 0.05));
  };
  video.onerror = () => { $("video-error").hidden = false; };
  video.src = result.video_url;
  $("viewer").showModal();
}
$("close-viewer").addEventListener("click", () => $("viewer").close());
$("viewer").addEventListener("close", () => { const video = $("video"); video.pause(); video.removeAttribute("src"); video.load(); });
function render(results) {
  $("results").replaceChildren(); $("empty").hidden = results.length > 0;
  results.forEach((result, rank) => {
    const card = element("button", "card"); card.type = "button";
    card.setAttribute("aria-label", `Xem ${result.video_id}, camera ${result.camera_id}, ${timeLabel(result.timestamp_sec)}`);
    const thumb = element("div", "thumbnail");
    const image = element("img"); image.src = result.image_url; image.loading = "lazy";
    image.alt = `Keyframe ${result.video_id} tại ${timeLabel(result.timestamp_sec)}`;
    thumb.append(image, element("span", "timestamp", timeLabel(result.timestamp_sec)));
    const body = element("div", "card-body"), top = element("div", "card-top"), bottom = element("div", "card-bottom");
    top.append(element("strong", "", result.video_id), element("span", "camera", result.camera_id));
    bottom.append(element("span", "", `#${rank + 1} · ${Number(result.score).toFixed(3)}`), element("span", "", "Xem video ↗"));
    bottom.title = "Độ tương đồng CLIP; không phải xác suất sự kiện";
    body.append(top, bottom); card.append(thumb, body); card.addEventListener("click", () => view(result)); $("results").append(card);
  });
}
async function search() {
  if (busy || !ready) return;
  error("");
  const topK = $("top-k").value;
  let url, options;
  if (mode === "text") {
    const query = $("query").value.trim();
    if (!query) { error("Nhập mô tả trước khi tìm kiếm."); $("query").focus(); return; }
    url = `/cctv/search?${new URLSearchParams({query, top_k: topK})}`;
  } else {
    const file = $("image-file").files[0];
    if (!file) { error("Chọn một ảnh mẫu trước khi tìm kiếm."); return; }
    if (file.size > 8 * 1024 * 1024) { error("Ảnh tối đa 8 MiB."); return; }
    url = `/cctv/search/image?top_k=${topK}`; options = {method: "POST", headers: {"Content-Type": file.type || "application/octet-stream"}, body: file};
  }
  setBusy(true); $("result-summary").textContent = "Đang tìm các cảnh tương đồng…";
  try {
    const response = await fetch(url, options); const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Truy vấn chưa hợp lệ. Kiểm tra ảnh hoặc mô tả.");
    render(data.results); $("result-summary").textContent = `${data.total} kết quả · Chọn một keyframe để xem video.`;
  } catch (problem) {
    error(problem.message || "Không thể hoàn tất tìm kiếm."); $("result-summary").textContent = "Tìm kiếm chưa hoàn tất.";
  } finally { setBusy(false); }
}
$("search-form").addEventListener("submit", event => { event.preventDefault(); search(); });
document.querySelectorAll("[data-query]").forEach(button => button.addEventListener("click", () => { selectMode("text"); $("query").value = button.dataset.query; search(); }));
checkReady();
