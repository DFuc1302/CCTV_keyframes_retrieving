# T6.1 — Annotation, splits và event evaluator

## Concept / architecture rationale

Evaluator kiểm tra annotation và split trước khi tính metrics. Annotation
độc lập khác với journal review của ứng viên: journal chỉ chứa case rule đã
chọn, nên không thể tự làm ground truth hoặc chứng minh recall.

Hiện chưa có nhãn CCTV độc lập. Hai config `cctv.annotations.pending.json`
và `cctv.splits.pending.json` là **template chưa review/chưa chia tập**:
sáu clip `[30,40)` giữ pending, events rỗng và split unassigned. Tất cả dùng
group `DEMO-UNVERIFIED` để không tự giả định tên file đại diện cho recording
session độc lập. Cần xác nhận grouping trước khi chia development/test.

Chạy template tạo `NOT_EVALUATED_NO_REVIEWED_INTERVALS`, metrics=null và
split_ready=false. Không coi template rỗng hoặc output rule rỗng là negative,
không tự gán accuracy=100%. Fixtures có camera/video `SIM-*` riêng, không
gán nhãn cho footage thật.

## Schemas / executable code

- `pipeline/cctv/benchmark_events.py`: annotation/split contracts, source
  binding, exact matching, metrics theo camera/label, sensitivity và CLI.
- `pipeline/cctv/benchmark_fixtures.py`: report phép tính tổng hợp độc lập
  với video/ground truth thật.
- `configs/cctv.annotations.pending.json`: sáu khoảng chưa được gán nhãn.
- `configs/cctv.splits.pending.json`: nhóm recording chưa được xác nhận.
- `test_cctv_benchmark.py`: 16 tests, oracle matching brute-force, metrics,
  synthetic exclusion, provenance, leakage và publication/resume safety.

Annotation schema gồm camera_id/video_id, start_sec/end_sec PTS tương đối,
review_status pending/unknown/reviewed, reviewed_by, reviewed_at có timezone,
blind_to_predictions và events. Review đầy đủ cần ít nhất hai reviewer khác
nhau; negative hợp lệ là interval **reviewed** có events=[].
Pending/unknown không chứa nhãn sự kiện. Các interval cùng video không được
overlap; event truth phải nằm trọn trong coverage và có ID duy nhất.

Ontology gồm congestion, road_incident, accident, moving_to_stop. Không coi
đứng yên là accident. Annotation reviewed được hiểu là đã xem đầy đủ các
loại mục tiêu trong ontology, không chỉ loại mà rule đang tạo ứng viên.
Reviewer names/blind/grouping status là provenance do người tạo nhãn khai
báo; validator không xác thực danh tính hoặc tự chứng minh annotation độc lập.

Split manifest bao phủ mỗi canonical video đúng một lần; cùng recording
group không được vào hai split. Group unverified chỉ được unassigned.
camera_holdout=true chặn cùng camera xuất hiện ở development và test.
Mode synthetic cần synthetic groups, không trộn provenance với independent.
Annotation/split/predictions phải bind cùng SHA256 source manifest.

## Matching / metrics

Chỉ xét prediction nằm trọn trong một interval reviewed của split được
chọn. Prediction băng qua boundary/unknown/chưa xem bị ignored và có reason;
không clip âm thầm hoặc tính FP ngoài coverage. Cần báo ignored count và
coverage để tránh diễn giải score trên vùng quan sát không đầy đủ.

`congestion_candidate` → congestion; `suspected_road_incident` → road_incident.
Không chuyển demo_case thành prediction thật. Synthetic prediction bị
ignored trong independent benchmark; ROI draft prediction vẫn có thể được
đánh giá trên nhãn độc lập, với demo count và operational_ready=false.

Temporal IoU = thời lượng giao/thời lượng hợp. Ghép cùng camera/video/label,
một-một, tối đa **tổng IoU** trong các cặp đạt ngưỡng. Đây là protocol T6.1
đã ghi trong evaluation plan, không phải tối đa cardinality trước IoU:
một cặp IoU 0.99 có thể thắng hai cặp tổng 0.62. Test giữ rõ khác biệt này.
Các prediction trùng không được nhiều lần ghép cùng truth; phần dư là FP.

DP chính xác theo truth bitmask, tối đa 16 predictions/truths mỗi video/label
và 2.000.000 bước tìm kiếm. Vượt giới hạn báo lỗi, không fallback greedy hay
xuất score một phần. Chạy sensitivity 0.1, 0.3, 0.5; 0.3 là protocol ban đầu
chưa được nghiệm thu bằng nhãn thật. Freeze protocol trước khi chạy test.

Precision=TP/(TP+FP), recall=TP/(TP+FN), F1=2TP/(2TP+FP+FN). Mẫu số 0 trả
null. Không tính TN/accuracy cho event intervals. False alarms/camera-hour
dùng FP chia tổng reviewed video duration/3600 và báo cả mẫu số giây.
Không đếm trùng intervals cùng video; các video khác nhau phải được con
người kiểm tra là recording không trùng bản sao/đoạn thời gian trước khi
diễn giải camera-hours, vì chưa có absolute timestamps để tự khử trùng.

Event v2 có confirmation frame làm trigger PTS. Delay là trigger_source_sec
trừ onset truth, với delay_basis=confirmation_evidence_pts. Đây là source
timeline delay của baseline, không phải wall-clock latency/dispatch response.
V1 không có timestamp phát ứng viên đầu tiên nên delay=null; không dùng
episode start/end hồi cứu làm trigger giả.

## PowerShell run/test

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'

& $python -m pipeline.cctv.benchmark_events `
  --manifest configs\cctv.demo.json `
  --annotations configs\cctv.annotations.pending.json `
  --splits configs\cctv.splits.pending.json `
  --events-dir outputs\demo-data\incidents-draft-v1 `
  --output outputs\demo-data\event-benchmark-pending-v1 --split test

# Chạy lại nguyên command: resumed=true, skip matching và giữ report/state.
Get-Content outputs\demo-data\event-benchmark-pending-v1\benchmark_report.json

& $python -m pipeline.cctv.benchmark_fixtures `
  --manifest configs\cctv.demo.json `
  --output outputs\demo-data\event-benchmark-fixtures-v1

& $python scripts\run_python_logged.py --log outputs\tests\benchmark-tests.txt -- `
  -m unittest -v test_cctv_benchmark
.\scripts\Test-Windows.ps1
```

Để gán nhãn thật, copy template sang output annotation riêng, ghi bằng chứng
review và grouping đã xác minh, không tự sửa pending thành reviewed để có
score. Khi annotations/splits thay đổi, dùng output benchmark mới; config/
labels/code/package SHA được đóng băng trong report. Input raw/config/event
chỉ đọc, output isolation và checksum guard bảo vệ artifacts cũ. Stage này
chạy CPU, không tải model và không thay requirements; không xử lý raw frames.

## Verification

Fixture có hai truth, một hit, một duplicate, một FP và một prediction ngoài
coverage: TP=1, FP=2, FN=1; precision=1/3, recall=1/2, F1=0.4. Coverage 10
giây làm rate=720 false alarms/camera-hour trong **bài tập số học tổng hợp**;
không diễn giải con số này thành hiệu năng hệ thống CCTV. Fixture delay=3
giây. Matching cũng được đối chiếu oracle liệt kê độc lập và greedy trap.

CCTV thật: chưa có reviewed interval hoặc split hợp lệ để benchmark. Report
không có precision/recall. Labels, raw và toàn bộ upstream packages giữ
nguyên. Bằng chứng local: `outputs/demo-data-review/benchmark-verification.json`.

## Git

```powershell
git add pipeline/cctv/benchmark_events.py pipeline/cctv/benchmark_fixtures.py `
  configs/cctv.annotations.pending.json configs/cctv.splits.pending.json `
  test_cctv_benchmark.py scripts/Test-Windows.ps1 docs/CCTV_BENCHMARK.md `
  docs/CCTV_EVENT_EVALUATION.md docs/CCTV_PROGRESS.md README_CCTV.md README.md
git commit -m 'feat: validate CCTV annotations and splits with temporal event evaluation'
git push origin main
```

Tiếp theo T6.2: đóng băng benchmark inputs/config và đo runtime/retrieval
latency; đánh giá chất lượng trên dữ liệu thật vẫn cần nhãn độc lập và
grouping/ROI calibration. T6.3 báo cáo/bàn giao máy thứ hai còn mở.
