# T6.3 — bàn giao và nghiệm thu máy thứ hai

## Phạm vi bàn giao

Code/config/docs/tests nằm trên GitHub. Video, keyframes, model cache, index
và reports nằm ngoài Git. Báo cáo [CCTV_PROJECT_REPORT.md](CCTV_PROJECT_REPORT.md)
tổng hợp kết quả kỹ thuật và giới hạn từ M0–T6.2. Máy thứ hai **chưa chạy**;
checklist dưới đây không phải bằng chứng đã nghiệm thu.

`scripts/export_cctv_handover.py` đọc ba publications đã hoàn tất: HTTP runtime
benchmark, event benchmark và dispatch simulation. Kiểm tra report SHA256,
source manifest association, input trước/sau; tạo package summary và template
máy thứ hai. Không nạp model, đọc nội dung video, chạy inference hoặc sửa review.
Package chỉ xác minh các reports đã seal, không chạy lại các pipeline tạo chúng.
Status/lỗi/null metrics được giữ nguyên, không đổi thành kết quả đạt.

## Xuất gói bằng chứng trên máy hiện tại

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m scripts.export_cctv_handover `
  --manifest configs\cctv.demo.json `
  --runtime-dir outputs\demo-data\runtime-benchmark-v1 `
  --event-dir outputs\demo-data\event-benchmark-pending-v1 `
  --dispatch-dir outputs\demo-data\dispatch-simulation-v1 `
  --output outputs\demo-data\handover-v1
```

Output gồm `handover_report.json`, `second-machine.pending.json`,
`handover_state.json`. Chạy lại cùng lệnh trả resumed=true, không ghi lại
bytes/mtime. Nếu input/exporter thay đổi dùng output mới. SHA phát hiện sửa
vô tình, không thay thế chữ ký bảo mật. Template luôn NOT_RUN/pending;
khi nghiệm thu thật, ghi kết quả vào **bản sao ngoài package đã seal**.

## Chuẩn bị máy thứ hai

Yêu cầu Windows 11 Native, PowerShell, Python 3.11 x64, Git, FFmpeg/ffprobe
trong PATH và NVIDIA CUDA tương thích runtime pinned. Dùng checklist máy
hiện tại để đối chiếu, không tự khẳng định máy mới có GPU/RAM đủ.

Clone repo vào folder mới, ghi commit đã chọn; không sao chép `.venv`.
Chuyển bản sao raw được phép sử dụng vào folder `Demo_Data` cạnh repo.
`configs/cctv.demo.json` resolve raw_root từ folder configs, hiện là
`../../Demo_Data`. Nếu dùng folder khác, tạo manifest mới và truyền đúng
manifest cho mọi stage; không sửa raw hoặc source manifest đã dùng benchmark.

```powershell
git clone https://github.com/DFuc1302/CCTV_keyframes_retrieving.git
Set-Location .\CCTV_keyframes_retrieving
git rev-parse HEAD
Get-Command py,git,ffmpeg,ffprobe
.\scripts\Setup-Windows.ps1 -Vision
.\scripts\Test-Preflight.ps1
.\scripts\Test-Windows.ps1
```

Lưu commit, model/package versions, GPU, PowerShell version và log. Core suite
không cần raw/model weights; khi thiếu dữ liệu thật vẫn có thể kiểm thử fixtures.
Không chạy demo/model khác trong lúc test hoặc stage YOLO/CLIP. Setup tải wheels;
không đổi execution policy hoặc pagefile hệ thống.

## Dựng lại retrieval từ raw — chạy từng stage

Đường dẫn tuyệt đối/code hash trong checkpoints/model descriptor phụ thuộc
máy tạo. Vì vậy dựng outputs mới ở máy thứ hai; không sửa thủ công sealed
checkpoints để đổi đường dẫn và không dùng report latency cũ làm phép đo mới.
Nếu chuyển cache model offline, chạy download helper offline để tạo descriptor
đúng máy; không sao chép descriptor chứa path của máy cũ.

```powershell
$python = Join-Path (Get-Location).Path '.venv\Scripts\python.exe'
& $python -m pipeline.cctv.manifest configs\cctv.demo.json --require-files
if ($LASTEXITCODE -ne 0) { throw 'Manifest validation failed.' }
& $python -m pipeline.cctv.ingest --manifest configs\cctv.demo.json `
  --output-dir outputs\demo-data\processed --device cpu
if ($LASTEXITCODE -ne 0) { throw 'Ingestion failed.' }
& $python -m scripts.download_cctv_clip
if ($LASTEXITCODE -ne 0) { throw 'CLIP download failed.' }
& $python -m pipeline.cctv.index --input-dir outputs\demo-data\processed `
  --output-dir outputs\demo-data\search-index `
  --model-config outputs\model-cache\clip.json --device cuda --batch-size 4
if ($LASTEXITCODE -ne 0) { throw 'Index failed.' }
& $python -m pipeline.cctv.preview --index-dir outputs\demo-data\search-index `
  --output-dir outputs\demo-data\video-preview
if ($LASTEXITCODE -ne 0) { throw 'Preview failed.' }
```

Chạy lại ingestion/index/preview với cùng lệnh để kiểm tra skip/checksum và
input/output mtime. Ghi SHA256 raw trước/sau bằng `Get-FileHash`; đối chiếu
ID/camera/video/PTS với map, không chỉ đếm số file.

## Dựng baseline traffic/events — trước khi khởi động server

Detection keyframe đầy đủ có thể chạy theo [CCTV_DETECTION.md](CCTV_DETECTION.md).
Tracking cần raw native frames, không phụ thuộc keyframe detection output.

```powershell
& $python -m scripts.download_cctv_yolo
if ($LASTEXITCODE -ne 0) { throw 'YOLO download failed.' }
& $python -m pipeline.cctv.track --manifest configs\cctv.demo.json `
  --traffic-config configs\cctv.traffic.demo.json `
  --model-config outputs\model-cache\yolo\yolo.json `
  --tracker-config configs\cctv.bytetrack.yaml `
  --output-dir outputs\demo-data\tracking-clips `
  --start-sec 30 --duration-sec 10 --segment-seconds 5 --device cuda
if ($LASTEXITCODE -ne 0) { throw 'Tracking failed.' }
& $python -m pipeline.cctv.congestion --manifest configs\cctv.demo.json `
  --roi-config configs\cctv.roi-draft.json `
  --metrics-config configs\cctv.congestion.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output-dir outputs\demo-data\traffic-draft-v1 --allow-draft
if ($LASTEXITCODE -ne 0) { throw 'Congestion failed.' }
& $python -m pipeline.cctv.incidents --manifest configs\cctv.demo.json `
  --roi configs\cctv.roi-draft.json --config configs\cctv.incidents.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\incidents-draft-v1 --allow-draft
if ($LASTEXITCODE -ne 0) { throw 'Incident rule failed.' }
& $python -m pipeline.cctv.dispatch --manifest configs\cctv.demo.json `
  --resources configs\cctv.resources.simulation.json `
  --demands configs\cctv.demands.simulation.json `
  --output outputs\demo-data\dispatch-simulation-v1
if ($LASTEXITCODE -ne 0) { throw 'Dispatch simulation failed.' }
& $python -m pipeline.cctv.benchmark_events --manifest configs\cctv.demo.json `
  --annotations configs\cctv.annotations.pending.json `
  --splits configs\cctv.splits.pending.json `
  --events-dir outputs\demo-data\incidents-draft-v1 `
  --output outputs\demo-data\event-benchmark-pending-v1
if ($LASTEXITCODE -ne 0) { throw 'Event benchmark validation failed.' }
```

Các configs annotation/split pending bind manifest checksum gốc. Nếu tạo
manifest mới, tạo annotation/split mới có checksum đúng, giữ pending và
unassigned cho đến khi review thật; không đổi pending thành reviewed chỉ
để chạy evaluator. Draft ROI và simulation vẫn phải hiển thị rõ.

## Demo và latency trên máy mới

```powershell
.\scripts\Start-CctvDemo.ps1
```

Terminal khác: `Invoke-RestMethod 'http://127.0.0.1:8080/ready'` phải ready=true.
Trên UI thử text, upload ảnh, mở video đúng timestamp, xem traffic timeline.
Chạy lệnh benchmark trong [CCTV_RUNTIME_BENCHMARK.md](CCTV_RUNTIME_BENCHMARK.md)
với output mới của máy thứ hai. Không yêu cầu latency giống máy đầu; ghi
environment, sample count, error count và P50/P95 mới. Dừng server bằng Ctrl+C
trước khi chạy core suite lần cuối. Xuất package theo lệnh đầu tài liệu, trỏ
tới các output mới, rồi điền bản sao template bằng kết quả thật.

## Điều kiện nghiệm thu và Git

Nghiệm thu công cụ xuất trên máy hiện tại: **189 core tests đạt**, CUDA
FP16/NMS và dependency check đạt. Package `outputs/demo-data/handover-v1`
giữ đúng latency samples, metrics=null, dispatch simulation và NOT_RUN.
Resume giữ nguyên hash/mtime ba JSON; 11 acceptance checks vẫn pending.
Bằng chứng cục bộ: `outputs/demo-data-review/handover-verification.json`.

| Check | Bằng chứng cần lưu |
| --- | --- |
| Environment/core | Commit, preflight, pip/runtime và core test log; lỗi phải ghi rõ |
| Raw/resume | SHA256+mtime trước/sau, skip counts và publication integrity |
| Retrieval/API | Count/dim, model recipe, ready, ID/PTS locator, text/image/video seek |
| Traffic/events | Native tracking scope, ROI pending flags, fixture results và metrics=null khi chưa có nhãn |
| Dispatch | Simulation flags, cap/reserve/skills và review gate; không lệnh điều động |
| Runtime | Workload/hash/session, sample/error counts, P50/P95 mới |

Không đánh dấu PASS khi thiếu log hoặc bước chưa chạy; giữ pending/fail kèm
lý do. Nghiệm thu demo kỹ thuật và nghiệm thu chất lượng là hai điều kiện
riêng. Nhãn độc lập, ROI review, camera locations và roster thật vẫn còn mở.

```powershell
git diff --check
git add scripts/export_cctv_handover.py test_cctv_handover.py `
  scripts/Test-Windows.ps1 docs/CCTV_PROJECT_REPORT.md docs/CCTV_HANDOVER.md `
  docs/CCTV_PROGRESS.md README_CCTV.md
git commit -m 'feat: add CCTV handover evidence package and reproduction guide'
git push origin main
```

## T9.4 — gói bằng chứng runtime hiện tại

`outputs/demo-data/handover-t9-v1` sử dụng runtime T9.4 của app `9ff3006`,
event benchmark pending và dispatch simulation; giữ gói lịch sử `handover-v1`.
Export và resume ba JSON đạt, SHA/mtime giữ nguyên. Checklist máy thứ hai
vẫn `NOT_RUN`; event metrics vẫn null do chưa có khoảng được review thật.
Chi tiết latency và kiểm chứng: `CCTV_RUNTIME_BENCHMARK.md`, mục T9.4.

Để xuất gói từ phép đo mới ở hướng dẫn T9.4:

```powershell
.\.venv\Scripts\python.exe scripts\export_cctv_handover.py `
  --manifest configs\cctv.demo.json `
  --runtime-dir outputs\demo-data\runtime-benchmark-t9-v2 `
  --event-dir outputs\demo-data\event-benchmark-pending-v1 `
  --dispatch-dir outputs\demo-data\dispatch-simulation-v1 `
  --output outputs\demo-data\handover-t9-v2

git diff --check
git add docs/CCTV_RUNTIME_BENCHMARK.md docs/CCTV_HANDOVER.md `
  docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md
git commit -m 'docs: record runtime benchmark and handover after overlay integration'
git push origin main
```
