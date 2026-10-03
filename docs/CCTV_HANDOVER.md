# T6.3 — bàn giao và nghiệm thu máy thứ hai

## Phạm vi bàn giao

Code/config/docs/tests nằm trên GitHub. Video, keyframes, model cache, index
và reports nằm ngoài Git. Báo cáo [CCTV_PROJECT_REPORT.md](CCTV_PROJECT_REPORT.md)
tổng hợp kết quả kỹ thuật hiện hành và bằng chứng lịch sử. Máy Windows vật lý
thứ hai **chưa chạy**; checklist/tooling không phải bằng chứng đã nghiệm thu.

`scripts/export_cctv_handover.py` đọc ba publications đã hoàn tất: HTTP runtime
benchmark, event benchmark và dispatch simulation. Kiểm tra report SHA256,
source manifest association, input trước/sau; tạo package summary và template
máy thứ hai. Không nạp model, đọc nội dung video, chạy inference hoặc sửa review.
Package chỉ xác minh các reports đã seal, không chạy lại các pipeline tạo chúng.
Status/lỗi/null metrics được giữ nguyên, không đổi thành kết quả đạt.

Runtime hiện hành: serving CLIP thuộc process riêng, close chờ process thoát
trước stage GPU; coordinator không import Torch. Windows đặt mặc định native
BLAS/OpenMP hai threads trước import, giữ overrides của operator. Không đổi
pagefile hoặc dùng CPU fallback. Cold local UI smoke mới đạt 12/12 lần đầu
trên test-pattern 12 giây (`outputs/tests/m11-cold-smoke.json`), không thay
nghiệm thu physical second host. Trên máy thứ hai vẫn chạy full-process/UI
checklist với output fresh; khi thiếu system commit, ghi lỗi thay vì đánh dấu đạt.

## Xuất gói bằng chứng trên máy hiện tại

**Cutover hiện hành:** pipeline dispatch là `cctv-dispatch-v2`. Các publications
`dispatch-simulation-v1`, `dispatch-events-empty-v1`, `dispatch-review-gate-v1`
và packages handover đã seal bên dưới là lịch sử; không sửa checkpoint/report
để chúng trông như v2. Regenerate dispatch vào folder mới theo các lệnh dưới;
demo store hiện hành mặc định đọc ba snapshot **v2** này. Giữ request/review
journal hiện có. Đường dẫn v2 là output sẽ tạo, **không** claim đã tồn tại.
Nếu folder có recipe cũ/khác, chọn output fresh khác và bind store tương ứng
qua `create_app(dispatch_store=...)`; scripts start/smoke mặc định dùng tên v2
đã ghi dưới đây, không có tham số Store override.
Runtime benchmarks lịch sử vẫn được giữ nguyên, không biến thành đo latency mới.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m scripts.export_cctv_handover `
  --manifest configs\cctv.demo.json `
  --runtime-dir outputs\demo-data\runtime-benchmark-v1 `
  --event-dir outputs\demo-data\event-benchmark-pending-v1 `
  --dispatch-dir outputs\demo-data\dispatch-simulation-v2 `
  --output outputs\demo-data\handover-current-v2
```

Output gồm `handover_report.json`, `second-machine.pending.json`,
`handover_state.json`. Chạy lại cùng lệnh trả resumed=true, không ghi lại
bytes/mtime. Nếu input/exporter thay đổi dùng output mới. SHA phát hiện sửa
vô tình, không thay thế chữ ký bảo mật. Template luôn NOT_RUN/pending;
khi nghiệm thu thật, ghi kết quả vào **bản sao ngoài package đã seal**.

## Kiểm chứng kỹ thuật bằng một lệnh

Sau setup môi trường, chuyển bản sao raw được phép và **inventory từ máy gốc**
sang máy nhận. Dùng `inventory.json` đã seal của tuyến T10.1, ví dụ
`outputs/evaluation/demo-inventory-v2/inventory.json`; giữ checksum file
inventory khi transfer. Reference cần `videos` với `video_id`, `camera_id`,
`bytes`, `sha256`; không tạo inventory “reference” từ máy nhận để tự so với nó.
Ghi hostname thật trên máy gốc (`hostname`) và pinned commit origin
(`git rev-parse HEAD`) trước khi transfer. Máy nhận chỉnh raw_root bằng
manifest mới nếu cần, không chỉnh reference inventory hoặc raw.

Chạy từ repo sau khi setup, ví dụ reference đã chuyển vào folder riêng:

```powershell
.\scripts\Test-SecondMachineHandover.ps1 `
  -ReferenceInventory 'D:\handover-inputs\inventory.json' `
  -OriginMachine 'ORIGIN-WINDOWS-HOST' `
  -Manifest configs\cctv.demo.json
if ($LASTEXITCODE -ne 0) { throw 'Technical handover verification failed; retain its package/log.' }
```

Thay `ORIGIN-WINDOWS-HOST` bằng hostname đã ghi trên origin.
`-ReferenceInventory` và `-OriginMachine` bắt buộc; `-Manifest` mặc định
`configs/cctv.demo.json`. Tùy chọn `-ExpectedCommit '<full-origin-HEAD-SHA>'`
để ràng buộc **full Git HEAD SHA**; không có thì ghi snapshot thực, không tự
claim commit parity. `-Output '<fresh-nonexistent-output-folder>'` tùy chọn;
mặc định tạo `outputs/second-machine-<UTCtimestamp>` mới. Output phải chưa tồn tại;
không dùng lại sealed/review/raw folders.

Command thu environment/code snapshot, đối chiếu source identity/size/SHA256
với inventory origin trước/sau, gọi **`scripts/Test-Windows.ps1` thật**:
pip check, runtime CUDA FP16/NMS, compile và full core suite. Chỉ evidence vừa
được tạo/cập nhật trong lần chạy được **copy**: `outputs/tests/core-tests.txt`
và `outputs/preflight/runtime-checks.json` vào `runtime-evidence/` trong package
fresh, không dùng sealed publications làm nơi ghi log. Output gồm:

- `second-machine-report.json`, `environment.json`, `verification.log`;
- `source-before.json`, `source-after.json`, `code-before.json`, `code-after.json`;
- bản sao `reference-inventory.json`, `checksums.json`;
- `second-machine-evidence.zip` và `archive-checksum.json`.

Không chứa raw/weights hay tự sửa review/calibration forms.
Đây là kiểm chứng kỹ thuật của pipeline/contracts và CUDA runtime, không
tự chạy/duyệt toàn bộ inference+UI hoặc tạo quality benchmark mới.

Local tooling rehearsal đã đạt tại `outputs/handover-verified-current/` (và rehearsal lịch sử `outputs/limits-handover-verified/`):
**358 tests/1 skip** (directory symlink không được OS cho phép), pip check,
compile, CUDA FP16/NMS đạt; 10 ZIP members và archive SHA khớp. Sáu raw,
manifest/reference và working-copy code giữ integrity trong lượt chạy.
Đây là local rehearsal, không second-machine run. Output phải nằm ngoài
`outputs/tests` và `outputs/preflight`, là nơi runtime probes ghi evidence.
Same-host rehearsal giữ `second_machine_status=NOT_RUN`. Khác hostname chỉ
ghi `host_identity_differs=true`; technical pass giữ
`second_machine_status=PENDING_HUMAN_VERIFICATION`, không chứng minh hardware
vật lý độc lập (hostname có thể đổi/ảo hóa). Luôn
`physical_host_verified=false`, `operational_ready=false`, `quality_metrics=null`,
M8 `PENDING`. Người thật xác nhận host vật lý khác và checklist full-process/UI
ở phần sau; không sửa technical report đã xuất để giả autoapproval.


## Chuẩn bị máy thứ hai

### T10.7 — tuyến nghiệm thu full-process UI

Sau setup môi trường bên dưới, người dùng demo xử lý dữ liệu bằng
`Open-CctvDemo.cmd` theo [CCTV_UI_WORKFLOW.md](CCTV_UI_WORKFLOW.md). Các lệnh
stage trong tài liệu này là tuyến tái lập kỹ thuật tùy chọn, không bắt buộc
người dùng UI phải chạy từng script.

1. Ghi pinned commit **hiện hành đã chọn** (M11 `bcc8e64` chỉ là lịch sử),
   máy/GPU/driver, Python/PowerShell,
   package versions và log setup/tests. Không sao chép `.venv` máy đầu.
2. Dùng bản sao demo được phép chuyển; đối chiếu SHA nguồn giữa hai máy.
   Mở launcher, chọn demo/upload và dùng cùng ROI draft/khoảng tracking.
3. Chạy pipeline trên UI. Lưu progress/log; kiểm tra thành công, retry/resume,
   raw integrity. Không yêu cầu inference GPU giống từng bit giữa hai máy.
4. Kiểm tra text/image query, keyframe/video seek, traffic/overlay/evidence,
   review needs_more_evidence và proposal bị gate chặn. Lưu evidence tại máy mới.
5. Tải ZIP từ UI; giữ recipe/model/source provenance, counts và flags demo.
   Đối chiếu evaluator counts chỉ khi có cùng nhãn/config đủ gate; không tạo
   quality metrics từ footage pending. Đo runtime riêng nếu nghiệm thu latency.
6. Điền **bản sao** `second-machine.pending.json` hiện có bằng log thực và
   đường dẫn evidence, không sửa template đã seal. Check chưa chạy giữ pending.

Hiện checklist chỉ được chuẩn bị; không có machine_id/tested_commit/log máy
thứ hai nên trạng thái vẫn **NOT_RUN**. ZIP M11 không đóng gói raw/weights;
chuyển dữ liệu/cache được phép và setup môi trường là công việc riêng.

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
& $python scripts\preview_cctv_roi.py --manifest configs\cctv.demo.json `
  --roi-config configs\cctv.roi-draft.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output-dir outputs\demo-data\roi-draft-previews
if ($LASTEXITCODE -ne 0) { throw 'ROI preview failed.' }
& $python -m pipeline.cctv.incidents --manifest configs\cctv.demo.json `
  --roi configs\cctv.roi-draft.json --config configs\cctv.incidents.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\incidents-draft-v1 --allow-draft
if ($LASTEXITCODE -ne 0) { throw 'Incident rule failed.' }
& $python -m pipeline.cctv.dispatch --manifest configs\cctv.demo.json `
  --resources configs\cctv.resources.simulation.json `
  --demands configs\cctv.demands.simulation.json `
  --output outputs\demo-data\dispatch-simulation-v2
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

## T9.5 — hoàn thiện assets cho các tab demo

Sau các stage trên, tạo package congestion và bài tập sự kiện tổng hợp riêng.
Case tổng hợp chỉ dùng để thử UI/review gate, không là nhãn phát hiện tai nạn.
Chạy trước khi mở server; không chạy lại vào thư mục có recipe khác.

```powershell
& $python -m pipeline.cctv.events --manifest configs\cctv.demo.json `
  --windows-dir outputs\demo-data\traffic-draft-v1 `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\events-v1
if ($LASTEXITCODE -ne 0) { throw 'Congestion event package failed.' }
& $python -m pipeline.cctv.events --manifest configs\cctv.demo.json `
  --windows-dir outputs\demo-data\traffic-draft-v1 `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\events-demo-v1 `
  --demo-video N001-V001 --demo-start-sec 30 --demo-end-sec 36
if ($LASTEXITCODE -ne 0) { throw 'Synthetic event package failed.' }
& $python -m pipeline.cctv.tracking_overlay --manifest configs\cctv.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\tracking-overlay-v2 --width 960
if ($LASTEXITCODE -ne 0) { throw 'Tracking overlay failed.' }
```

Dispatch simulation đã tạo ở trên. Thêm snapshot từ package incidents
(không yêu cầu journal tồn tại; chưa review giữ pending):

```powershell
& $python -m pipeline.cctv.dispatch --manifest configs\cctv.demo.json `
  --resources configs\cctv.resources.simulation.json `
  --demands configs\cctv.demands.events.json `
  --events-dir outputs\demo-data\incidents-draft-v1 `
  --review-dir outputs\demo-data\incident-reviews-v1 `
  --output outputs\demo-data\dispatch-events-empty-v2
if ($LASTEXITCODE -ne 0) { throw 'Incident dispatch snapshot failed.' }
```

Tab Phân bổ còn đọc request của case tổng hợp từ output riêng ngoài Git.
Tạo request bằng ID thực trong package mới; giữ file đã có nếu nội dung
khớp và từ chối ghi đè nếu khác. Không tạo/sửa review journal.

```powershell
$demoEvents = @(Get-Content outputs\demo-data\events-demo-v1\events.jsonl |
  ForEach-Object { $_ | ConvertFrom-Json } |
  Where-Object { $_.synthetic_event -and $_.video_id -eq 'N001-V001' })
if ($demoEvents.Count -ne 1) { throw 'Expected exactly one synthetic demo event.' }
$requestPath = Join-Path (Get-Location).Path 'outputs\demo-data-review\dispatch-event-request.json'
$writeRequest = @'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
request = {
    "schema_version": 1, "mode": "simulation", "kind": "event_package",
    "demands": [{"demand_id": "SIM-EVENT-REVIEW", "event_id": sys.argv[2],
                 "min_officers": 2, "required_skills": ["scene_assessment"],
                 "priority": 4, "max_travel_sec": 180.0}]
}
if path.exists():
    if json.loads(path.read_text(encoding="utf-8-sig")) != request:
        raise ValueError("Existing request differs; preserve it and use a separate recipe")
else:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request, indent=2), encoding="utf-8")
'@
$writerPath = Join-Path (Get-Location).Path ('outputs\demo-data-review\write-request-' + [Guid]::NewGuid().ToString('N') + '.py')
[System.IO.Directory]::CreateDirectory((Split-Path -Parent $writerPath)) | Out-Null
[System.IO.File]::WriteAllText($writerPath, $writeRequest, [System.Text.UTF8Encoding]::new($false))
& $python $writerPath $requestPath $demoEvents[0].event_id
if ($LASTEXITCODE -ne 0) { throw 'Synthetic dispatch request failed.' }
& $python -m pipeline.cctv.dispatch --manifest configs\cctv.demo.json `
  --resources configs\cctv.resources.simulation.json `
  --demands outputs\demo-data-review\dispatch-event-request.json `
  --events-dir outputs\demo-data\events-demo-v1 `
  --review-dir outputs\demo-data\events-demo-reviews-v1 `
  --output outputs\demo-data\dispatch-review-gate-v2
if ($LASTEXITCODE -ne 0) { throw 'Synthetic review-gate snapshot failed.' }
```

Máy mới chưa review: case có `pending_review`, không được gán đội. Máy hiện
tại có journal bài tập riêng với `needs_more_evidence`; giữ trạng thái hiện
có, không replay lịch sử để làm giống số revision. Thay đổi journal/resource/
request làm snapshot cũ stale; dùng output recipe mới và cấu hình store phù
hợp thay vì sửa checkpoint. CLI review tùy chọn xem `CCTV_EVENTS.md`.

Kiểm tra **lịch sử** T9.5 trên máy hiện tại (trước MILP cutover): sáu CLI lệnh ROI/events/overlay/dispatch
resume đạt; 76 protected files giữ SHA/mtime (xem số đếm thực trong
`outputs/demo-data-review/handover-steps-verification.json`). Rehearsal
CPU riêng với journal không tồn tại có 0 assignments, 1 unmet demand ở
`pending_review`, không tạo journal. Đây là kiểm tra cục bộ, máy thứ hai
vẫn NOT_RUN. T9.5 chỉ sửa tài liệu, core 259 tests đã đạt tại T9.3.
Mười một khối PowerShell trong tài liệu parse đạt; khối tạo request và snapshot
review gate đã thực thi trên Windows PowerShell 5.1, giữ request/journal cũ.

Sau T9.6, `Test-CctvDemo.ps1 -RequireOverlays` kiểm tra assets vừa dựng mà
không cần journal demo hoặc runtime report lịch sử. Reviews chưa có giữ
pending; journal xuất hiện trong lúc check làm run thất bại. Vẫn cần đầy đủ
index/previews/traffic/ROI/events/dispatch/request/overlay theo các stage
trên. Không tự tạo review để vượt smoke hoặc dùng kết quả cục bộ thay bằng
nghiệm thu máy thứ hai.

Sau cutover MILP, các counts/resume/hash T9.5 trên không xác nhận snapshot v2.
Regenerate cả ba dispatch outputs fresh theo tên v2 bên dưới trước khi dùng
scripts start/smoke mặc định; không dùng sealed v1 chỉ để vượt asset smoke.
Full-process workflow tạo publication mới riêng trong run, không rewrite v1.
Queue-context vẫn không loại xe đơn lẻ chờ đèn đỏ; stitching chỉ hình học,
không visual re-ID. km/h chỉ khi có surveyed calibration, N001/N002 pending/null;
19-term expansion không general translation. 100×100 MILP có precision guard
và numerical certificate, warned legacy fallback chỉ khi cả team/demand ≤12;
benchmark 25×25 không bảo đảm thời gian mọi scenario. Tất cả quality gates giữ.


Commit tài liệu sau kiểm chứng:

```powershell
git diff --check
git add README_CCTV.md docs/CCTV_HANDOVER.md docs/CCTV_PROGRESS.md `
  docs/CCTV_PROJECT_REPORT.md
git commit -m 'docs: complete CCTV demo asset rebuild instructions'
git push origin main
```

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

Nghiệm thu **lịch sử** công cụ xuất trên máy hiện tại: **189 core tests đạt**, CUDA
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

## Lịch sử T9.4 — gói bằng chứng runtime

`outputs/demo-data/handover-t9-v1` sử dụng runtime T9.4 của app `9ff3006`,
event benchmark pending và dispatch simulation; giữ gói lịch sử `handover-v1`.
Export và resume ba JSON đạt, SHA/mtime giữ nguyên. Checklist máy thứ hai
vẫn `NOT_RUN`; event metrics vẫn null do chưa có khoảng được review thật.
Chi tiết latency và kiểm chứng: `CCTV_RUNTIME_BENCHMARK.md`, mục T9.4.

Để đóng gói phép đo T9.4 với dispatch **được regenerate v2**, dùng output mới:

```powershell
.\.venv\Scripts\python.exe scripts\export_cctv_handover.py `
  --manifest configs\cctv.demo.json `
  --runtime-dir outputs\demo-data\runtime-benchmark-t9-v2 `
  --event-dir outputs\demo-data\event-benchmark-pending-v1 `
  --dispatch-dir outputs\demo-data\dispatch-simulation-v2 `
  --output outputs\demo-data\handover-t9-dispatch-v2

git diff --check
git add docs/CCTV_RUNTIME_BENCHMARK.md docs/CCTV_HANDOVER.md `
  docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md
git commit -m 'docs: record runtime benchmark and handover after overlay integration'
git push origin main
```
