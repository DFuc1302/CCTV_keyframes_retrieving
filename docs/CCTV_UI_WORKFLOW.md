# M11 — xử lý demo qua một UI

Dữ liệu của project là demo. Mục tiêu M11 là người dùng chọn video, chuẩn bị,
chạy pipeline, tra cứu, review và xuất báo cáo trong cùng giao diện. M8 vẫn
pending; review demo một người không tạo ground truth độc lập.

## Mở và sử dụng

Sau khi môi trường Windows được cài bằng `scripts/Setup-Windows.ps1 -Vision`,
double-click **Open-CctvDemo.cmd** tại thư mục repo. Launcher mở browser,
tái sử dụng server CCTV trên cổng 8080 hoặc khởi động một server local.
Nếu cổng thuộc ứng dụng khác, launcher báo lỗi và giữ nguyên ứng dụng đó.

1. Bấm **Dùng demo có sẵn**, hoặc **Tạo bộ upload** rồi chọn video H.264.
   Tối đa sáu video, mỗi video 1 GiB. Video gốc không bị sửa.
2. Gán camera; chọn ảnh tham chiếu, click ít nhất ba đỉnh ROI cho từng camera.
   Giữ chuột trên đỉnh và kéo để chỉnh theo vùng đường; thả chuột để kết thúc.
   Vùng tự đóng, không cần click lại điểm đầu. Kéo ra ngoài ảnh sẽ dừng ở biên;
   thả trùng đỉnh khác hoặc hủy thao tác giữ vị trí cũ. Bấm **Lưu camera và ROI**
   để lưu các thay đổi; kéo đỉnh chưa tự ghi cấu hình lên server.
   Nút **Toàn ảnh để chạy thử** hỗ trợ kiểm thử nhanh bằng bàn phím.
   Mặc định tracking toàn video, lấy một frame mỗi 0,5 giây. Chế độ theo khoảng
   dùng chung cho mọi video, dài 1–60 giây; vẫn có lựa chọn xử lý mọi frame.
   Lưu chuẩn bị. ROI giữ trạng thái pending.
3. Chọn một trong **hai tùy chọn xử lý video**:
   - **Tùy chọn A — Chạy trên thiết bị (On-Device)**:
     Bấm **Chạy toàn bộ pipeline**. Hệ thống sử dụng GPU/CPU máy cục bộ tuần tự qua 12 công đoạn.
     Nếu thiếu weights, dùng nút **Tải mô hình còn thiếu**.
     Theo dõi tiến độ và nhật ký; nút **Dừng** giữ checkpoint và **Thử lại** tiếp tục từ điểm dừng.
   - **Tùy chọn B — Chạy qua Google Colab (Cloud GPU)**:
     Chuyển sang tab **Chạy qua Google Colab (Cloud GPU)**:
     1. Bấm **Xuất job Colab (.zip)** để tải gói tác vụ chứa video, ROI và mã nguồn.
     2. Bấm **Mở notebook Colab ↗** để mở Google Colab (chạy trên GPU Tesla T4 miễn phí), đưa file zip lên và chạy tự động.
     3. Sau khi Colab hoàn tất và tải về file `.result.zip`, quay lại UI và bấm **Chọn file kết quả** tại mục Nhập kết quả Colab.
        Hệ thống tự động kiểm tra toàn vẹn SHA256 và kích hoạt toàn bộ kết quả vào giao diện.
4. Mở kết quả trong các tab: tìm keyframe bằng text/ảnh, xem video tại mốc
   tương ứng, traffic windows, ROI và video tracking bbox/ID.
5. Trong **Sự kiện & evidence**, chọn case tổng hợp, nhập reviewer/lý do và
   lưu quyết định demo. Tính proposal với số người, ưu tiên và kỹ năng;
   kiểm tra kết quả trong **Phân bổ mô phỏng**.
6. **Tải báo cáo ZIP** chứa summary và các JSON nhỏ hơn hoặc bằng 4 MiB;
   không đóng gói video, weights hoặc embeddings. Summary ghi M8 pending,
   `demo_only=true`, `operational_ready=false`.

## Thiết kế và giới hạn

### Tự kiểm chứng T10

Sau bước pipeline, bấm **Tự kiểm chứng T10** để chạy năm kiểm tra kỹ thuật
trên kết quả đã có: inventory, ROI/schema, evaluator fixture tổng hợp, CPU
rules/solver/workflow tests và HTTP smoke. UI hiển thị tiến độ 0–5, lỗi và
các phần pending. **Dừng** dừng worker; **Thử lại T10** tiếp tục checkpoint
cùng recipe. Nếu code/input/server đổi, bấm **Tự kiểm chứng T10** tạo lượt mới.

Không inference lại, không tự duyệt ROI/groups hay tạo nhãn độc lập. Một worker
dùng chung với pipeline; nếu đổi dataset, model phục vụ được thay tuần tự.
Kết quả kiểm chứng có metadata riêng, giữ pipeline job/run. Khi pipeline run
đổi, kết quả cũ hiển thị stale và cần kiểm chứng lại.
**Tải báo cáo ZIP** thêm `t10/report.json`, `t10/report.md`, checkpoint và
logs nhỏ của lượt đã hoàn tất cho run hiện tại. M8 pending, chất lượng footage
chưa đo và máy thứ hai NOT_RUN vẫn hiển thị rõ.

Kiểm chứng bổ sung ngày 2026-10-01: năm bước đạt trên M11 browser smoke,
46 CPU tests trong worker; Dừng → Thử lại T10 giữ cùng job và hoàn tất 5/5.
ZIP tải bằng nút UI chứa report JSON/Markdown và logs. Windows regression:
268 core tests đạt, pip check/compile/CUDA FP16/NMS/JS syntax đạt.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Test-Windows.ps1
node --check .\static\cctv\workflow.js
node .\scripts\test_cctv_roi.cjs
git diff --check
git add app/cctv_workflow.py static/cctv/index.html static/cctv/workflow.js test_cctv_workflow.py
git commit -m 'feat: expose T10 demo verification and reports in the UI'
git add docs/CCTV_UI_WORKFLOW.md docs/CCTV_M10_EVALUATION.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md
git commit -m 'docs: distinguish automated T10 demo checks from pending evaluation'
git push origin main
```

Backend gọi lại 12 CLI stage hiện có, không thay schema hoặc guard bảo vệ
dữ liệu. Dataset/job dùng JSON; một worker local tuần tự dừng và chờ process
CLIP phục vụ thoát trước khi index/tracking dùng GPU. Coordinator không import
Torch; model/ CUDA context thuộc process `ProcessClipEncoder`, vector CPU và
ảnh PIL đi qua IPC. CLI vẫn dùng cùng weights, CUDA FP16 và batch 4; tracking theo
segment. Generated files nằm trong `outputs/ui-workflow/<dataset>/run-*/`;
cấu hình trong `run-*/configs`, tách khỏi thư mục output của các stage.

Windows đặt mặc định `OPENBLAS_NUM_THREADS=2` và `OMP_NUM_THREADS=2` trước khi
import thư viện số để giới hạn private commit của native thread pools. Giá trị
operator đã đặt được giữ nguyên; thay đổi này không sửa pagefile hoặc tự fallback
CPU. RSS/GPU allocator trống không chứng minh Windows system commit đã giải phóng.
Smoke lifecycle CUDA: coordinator ~215 MiB private, worker thoát exit=0 trước CLI
loader; vector text/image so với loader trực tiếp có max error=0. Evidence:
`outputs/tests/m11-process-lifecycle.json`. Latency cũ không phải phép đo IPC mới.

Upload tạo bản sao riêng; demo có sẵn tham chiếu `Demo_Data` chỉ đọc.
Mutation yêu cầu same-origin; giới hạn JSON 64 KiB, filename và đường dẫn
được kiểm tra. Review dùng revision guard; proposal dùng solver và journal
guard hiện có. Team, vùng và travel times đều giả định. Ngưỡng traffic dùng
baseline demo, chưa được calibration.

Khi restart, job dang dở thành interrupted; chọn dataset đã lưu để retry
hoặc mở kết quả. Không tự chạy pipeline khi mở trang. Không hỗ trợ nhiều
server/worker cùng ghi workspace; launcher dùng một uvicorn worker.
Không bổ sung OCR/ASR hay benchmark chất lượng khi chưa có nhãn độc lập.

## Kiểm chứng trên Windows

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Test-Windows.ps1
node --check .\static\cctv\workflow.js
node --check .\static\cctv\events.js
git diff --check
```

Smoke thực 2026-10-01: video H.264 tổng hợp 12 giây upload qua HTTP local;
UI lưu ROI/10 giây tracking, chạy 12/12 stage, tạo 24 keyframes, index CLIP
768D, 250 frame tracking, 5 traffic windows và MP4 overlay. Text query trả
12 kết quả; browser mở video seek giây 8, duration 12, readyState 4.
Review needs_more_evidence revision 1 cho case tổng hợp; proposal 0 assignment,
1 unmet theo review gate. ZIP được tải qua nút UI. Upload/file bounds,
failure/retry cùng run và shutdown không reload model có unittest.
Kiểm thử hồi quy: **264 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt;
Ruff và kiểm tra cú pháp JavaScript đạt. PowerShell đọc launcher từ disk bằng
parser Windows PowerShell để bắt lỗi encoding/terminator.

Smoke mới 2026-10-03 dùng file input trong managed Chromium: upload H.264 12 giây,
pending ROI/toàn video 2 FPS, 12/12 stage lần đầu không retry/recovery; text/image
mỗi loại 12 kết quả, source/overlay readyState=4, report ZIP HTTP200/CRC đạt.
Evidence: `outputs/tests/m11-cold-smoke.json`; không đo chất lượng trên test-pattern.
44 focused tests đạt; full Windows verification: 353 tests, 352 passed/1 skipped
(directory symlink bị OS từ chối); pip check/compile/CUDA FP16/NMS đạt.
Logs: `outputs/tests/m11-process-core-tests.txt`, `m11-regression.json`.
Native hộp thoại file chooser OS vẫn chưa kiểm thử; không thay quyền browser.
Người dùng mở UI bằng Chrome thông thường không cần extension để chọn file.

```powershell
git add app/cctv.py app/cctv_workflow.py static/cctv scripts/Open-CctvDemo.ps1 scripts/Test-Windows.ps1 Open-CctvDemo.cmd test_cctv_workflow.py test_powershell_scripts.py README.md docs/CCTV_UI_WORKFLOW.md docs/CCTV_PROGRESS.md
git commit -m 'feat: process CCTV demos end to end through the UI'
git push origin main
```
