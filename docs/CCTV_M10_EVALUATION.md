# M10 — dữ liệu độc lập, đánh giá sự kiện và tái lập

## Phạm vi và cập nhật nguồn lực

M9 hoàn tất; M10 đánh giá congestion/road-incident trước, chưa thêm benchmark
relevance retrieval. Ngày 2026-10-01 người dùng xác nhận chỉ một reviewer,
giữ M8 pending và chờ thêm thông tin. Không dùng hai tên/slots của cùng một
người làm hai reviewer độc lập. Máy thứ hai chưa có bằng chứng truy cập/chạy.
Không đổi gate của M8 để biến pilot một người thành ground truth độc lập.

## T10.1 — kiểm kê kỹ thuật đã triển khai, review nội dung/groups còn pending

Code hoàn chỉnh: `scripts/inventory_cctv_evaluation.py`. Dùng ffprobe helper
hiện có để đọc codec/duration/first decoded PTS và PyAV để đọc dimensions,
nominal FPS/time base. Chỉ đọc raw; không chạy YOLO/CLIP hoặc gán nhãn.
SHA/mtime nguồn được kiểm tra trước/sau; checkpoint từng video, lỗi một
video được ghi và các video khác vẫn xử lý. Run lỗi không publish forms
hoàn chỉnh; chạy lại retry video lỗi. Publication đã seal không ghi lại.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe scripts\run_python_logged.py `
  --log outputs\tests\evaluation-inventory.txt -- `
  -m scripts.inventory_cctv_evaluation `
  --manifest configs\cctv.demo.json `
  --output outputs\evaluation\demo-inventory-v2
if ($LASTEXITCODE -ne 0) { throw 'Evaluation inventory failed.' }
```

Giữ nguyên recipe khi resume; manifest/code/raw đổi dùng output mới.
Video mới cần đăng ký vào manifest mới trước khi kiểm kê; không sửa
manifest đã dùng để seal các outputs cũ. Script chỉ kiểm kê videos trong
manifest, không tự đăng ký files ngoài manifest hoặc suy camera/session.

Output gồm năm JSON:

- `inventory.json`: metadata và checksum nguồn.
- `content.pending.json`: biểu mẫu content/session/overlap/view/exposure để người xem ghi.
- `splits.pending.json`: schema hiện có, tất cả unverified/unassigned.
- `roi.pending.json`: schema hiện có, polygon null, không phê duyệt tự động.
- `inventory_state.json`: checkpoint và artifact hashes.

Đây là templates đã seal. Chỉ chỉnh **bản sao working ngoài package**;
không sửa inventory/state/templates trực tiếp. Giữ tên reviewer thật,
thời gian có timezone và bằng chứng nhóm/góc nhìn; unknown vẫn unknown.
ROI geometry tham khảo ở config draft/tư liệu M8 hiện có, không tự copy
approval từ draft. Phê duyệt ROI do người dùng thực hiện riêng.

```powershell
$working = 'outputs\evaluation\demo-review-working-v1'
if (Test-Path -LiteralPath $working) { throw 'Working folder already exists; preserve existing review.' }
New-Item -ItemType Directory -Path $working | Out-Null
Copy-Item -LiteralPath outputs\evaluation\demo-inventory-v2\content.pending.json -Destination $working
Copy-Item -LiteralPath outputs\evaluation\demo-inventory-v2\splits.pending.json -Destination $working
Copy-Item -LiteralPath outputs\evaluation\demo-inventory-v2\roi.pending.json -Destination $working
```

Đã kiểm kê sáu MOV hiện có, cùng SHA với inventory gốc; chưa thấy video mới
ngoài sáu files đó trong Demo_Data. Tổng container duration 3.614,160 giây
(khoảng 60,24 phút), tất cả 1920×1080, nominal FPS gần 25. First decoded
PTS=0,2 giây; annotation dùng timestamp tương đối frame đầu, không dùng
container timestamp như thời gian ghi hình tuyệt đối. Duration này không
phải reviewed camera-hours hoặc thời lượng tracking (tracking demo chỉ 60 giây).

Demo output `outputs/evaluation/demo-inventory-v2`; resume năm JSON giữ
SHA/mtime, không probe lại; SHA sáu raw files khớp inventory gốc. Log:
`outputs/tests/m10-inventory-demo-v2.txt`, `m10-inventory-resume.txt`.
Test synthetic kiểm tra retry, schemas pending, metadata, resume, raw
read-only, overlap guard và edited publication rejection:

```powershell
.\.venv\Scripts\python.exe scripts\run_python_logged.py `
  --log outputs\tests\evaluation-inventory-unit.txt -- `
  -m unittest -v test_cctv_evaluation_inventory
.\scripts\Test-Windows.ps1
```

Core Windows suite đạt **261 tests**, pip check/compile/CUDA FP16/NMS đạt.
Các khối PowerShell trong tài liệu parse đạt. Bản sao ba forms đã tạo tại
`outputs/evaluation/demo-review-working-v1`; không chạy lại lệnh copy vào
folder này để tránh ghi đè quan sát người dùng. Review vẫn pending.

## Các bước tiếp theo và điều kiện bắt đầu

| Task | Đầu ra và gate | Trạng thái hiện tại |
| --- | --- | --- |
| T10.1 | Inventory kỹ thuật + nội dung/groups có bằng chứng | Kỹ thuật đã có; content/groups pending |
| T10.2 | Polygon reviewed trên nhiều thời điểm; tên/thời gian duyệt, camera đổi góc có ID riêng | Forms pending, chưa có approval |
| T10.3 | Protocol và split group-level, mục tiêu 60/40 theo duration; tối thiểu hai groups mỗi tập | Chưa có groups verified |
| T10.4 | Hai notes blind độc lập, explicit adjudication/export theo M8 | User yêu cầu M8 pending; một reviewer |
| T10.5 | Baseline và bounded CPU calibration trên development | Chưa có labels/ROI/split đủ điều kiện |
| T10.6 | Frozen test benchmark, FP/FN/ignored evidence | Chờ T10.3–T10.5 |
| T10.7 | Rebuild máy Windows NVIDIA khác, commit/env/log/checklist thực | Chờ thông tin máy thứ hai |
| T10.8 | Report và kịch bản bảo vệ source-backed | Báo cáo cập nhật M11 + kịch bản demo 10 phút/Q&A đã chuẩn bị; quality/second-machine pending |

Một reviewer có thể ghi quan sát/content pilot trước; không export reviewed
independent annotation. Nếu không đủ positive/negative hoặc groups độc lập,
bổ sung dữ liệu thay vì dùng fixtures hoặc đổi unknown thành negative.
M8 đã có prepare/review-notes/adjudication; không thêm workflow mới lúc này.

## Tiếp tục T10.8 — báo cáo và chuẩn bị bảo vệ demo

Theo phạm vi mới, mọi dữ liệu là demo, người dùng làm full process trên UI.
[CCTV_PROJECT_REPORT.md](CCTV_PROJECT_REPORT.md) đã cập nhật đến M11
`bcc8e64`, sửa mô tả UI chỉ đọc cũ, bổ sung luồng worker/GPU và phân biệt
smoke video tổng hợp với đánh giá chất lượng CCTV. Mục 7 có kịch bản khoảng
10 phút, phương án dataset đã xử lý và Q&A dựa trên bằng chứng hiện có.
[CCTV_HANDOVER.md](CCTV_HANDOVER.md) bổ sung checklist T10.7 qua UI sau setup,
tái sử dụng template máy thứ hai, không tạo workflow/code mới.

Đối chiếu local: job M11 complete 12/12, index 24/768D, tracking 250 frames/
2 segments, 5 windows/ROI pending/ngưỡng chưa calibration, proposal 0/1.
Core log 264 tests đạt; máy thứ hai template NOT_RUN. Không chạy lại GPU
hoặc core tests cho thay đổi tài liệu. Đây là chuẩn bị báo cáo/bảo vệ, chưa
phải buổi bảo vệ thực hoặc phép đánh giá độc lập. T10.1 content/groups,
T10.2 approval, T10.3 split và T10.5–T10.7 giữ trạng thái pending.
Kiểm chứng tài liệu: 8 local links tồn tại, 17 PowerShell blocks parse đạt,
artifact counts/pending flags khớp; log `outputs/tests/t10-report-verification.json`.

## Protocol calibration/test đã chốt, chưa thực thi

- Calibration dùng `development` của schema; test khóa theo recording group,
  không chia overlap/session xuyên tập, camera_holdout=false trong pilot.
- Giữ ontology congestion/road_incident/accident/moving_to_stop; chỉ headline
  các loại rule thực sự dự đoán. Không đồng nhất moving-to-stop với accident.
- Congestion: occupancy 0,20/0,25/0,30 × motion 8/12/16 px/s × persistence
  4/6/8 giây = 27 recipes; các tham số khác giữ baseline.
- Incident: stationary threshold 0,02/0,04/0,06 × duration 1/2/3 giây = 9
  recipes; tham số khác giữ baseline. Tái sử dụng tracks, không inference lại.
- Chọn riêng mỗi rule theo label F1 development tại IoU 0,3; hòa thì FP/h
  thấp hơn, khoảng cách tương đối tổng tới baseline thấp hơn, cuối cùng
  thứ tự số tăng dần. Undefined F1 không đủ điều kiện chọn.
- Test báo counts, precision/recall/F1, reviewed duration, FP/h và coverage
  bị loại, per-label/per-camera, sensitivity IoU 0,1/0,3/0,5. Giới hạn 16
  predictions/truths mỗi video/label: dừng rõ lý do, không cắt mẫu âm thầm.
- Test không tham gia chọn config; nếu chỉnh dựa trên test, cần test mới.
  Evidence cuối là proxy trigger, không phải thời gian phát cảnh báo thực.
- Máy thứ hai tạo venv/descriptor/outputs mới, giữ nguồn SHA và pinned commit;
  evaluator cùng inputs phải có cùng counts/matching. Không yêu cầu latency
  hoặc GPU inference giống từng bit, không đánh dấu NOT_RUN thành PASS.

Runner calibration chỉ triển khai khi gates dữ liệu/nhãn đã đáp ứng theo
chỉ dẫn mới của người dùng; hiện không có phép đo độc lập để chạy hoặc chọn
threshold. Không thêm model/UI/dependency trong bước chuẩn bị này.

```powershell
git diff --check
git add docs/CCTV_PROJECT_REPORT.md docs/CCTV_HANDOVER.md `
  docs/CCTV_M10_EVALUATION.md docs/CCTV_PROGRESS.md
git commit -m 'docs: prepare CCTV demo defense and UI reproduction checklist'
git push origin main
```
