# M10 — dữ liệu độc lập, đánh giá sự kiện và tái lập

## Phạm vi và cập nhật nguồn lực

### Tuyến tự kiểm chứng demo qua UI

Theo yêu cầu mới, mục tiêu hiện tại là chứng minh dữ liệu demo đi qua một UI.
Sau khi pipeline hoàn tất, bấm **Tự kiểm chứng T10**. Worker dùng lại output,
không chạy lại CLIP/YOLO hoặc thay quyết định review. Năm bước gồm inventory,
kiểm tra ROI/schema, phép tính evaluator trên fixture tổng hợp, CPU tests
cho rules/solver/workflow và HTTP smoke trên dataset đang phục vụ.

`scripts/verify_cctv_t10.py` tái sử dụng các verifier hiện có. Checkpoint và
checksum cho phép **Dừng → Thử lại T10** bỏ qua bước đã đạt; code/input/server
đổi cần lượt kiểm chứng mới. Mỗi lượt nằm riêng trong
`outputs/ui-workflow/<dataset>/t10-*/`, giữ nguyên pipeline run và raw.
JSON/Markdown cùng log nhỏ được thêm vào **Tải báo cáo ZIP**.

`PASSED_DEMO_CHECKS` chỉ xác nhận các kiểm tra kỹ thuật đã đạt. Report giữ
`demo_only=true`, `operational_ready=false`, `real_accuracy_measured=false`,
`quality_metrics=null`, M8 `PENDING`, máy thứ hai `NOT_RUN`.
T10.1 review/groups, T10.2 ROI approval, T10.3 split, T10.5 calibration và
T10.6 chất lượng footage vẫn pending. Fixture metrics không phải accuracy CCTV.
Các gates và protocol đánh giá độc lập bên dưới vẫn áp dụng khi có dữ liệu/nhãn.

M9 hoàn tất; M10 đánh giá congestion/road-incident trước, chưa thêm benchmark
relevance retrieval. Ngày 2026-10-01 người dùng xác nhận chỉ một reviewer,
giữ M8 pending và chờ thêm thông tin. Không dùng hai tên/slots của cùng một
người làm hai reviewer độc lập. Máy thứ hai chưa có bằng chứng truy cập/chạy.
Không đổi gate của M8 để biến pilot một người thành ground truth độc lập.

### Cập nhật kỹ thuật 2026-10-03 — không thay các gates đánh giá

Năm giới hạn engineering đã được xử lý: queue-context incident filtering;
optional geometric track stitching; ground-plane homography speed khi có
surveyed calibration; expansion 19 cụm CCTV Việt–Anh; certified numerical
MILP tối đa 100 team × 100 demand, warned legacy fallback chỉ khi cả hai ≤12.
Chi tiết chứng cứ và giới hạn:
[ma trận kỹ thuật/phương pháp](CCTV_PROJECT_REPORT.md#giới-hạn-kỹ-thuật-đã-xử-lý-và-ranh-giới-chưa-thể-nghiệm-thu).

Không đồng nhất các cải tiến với accuracy: một xe chờ đèn đỏ vẫn confounder;
geometric IDs không validated visual re-ID/count quality; N001/N002 chưa khảo
sát nên km/h null; expansion không dịch Việt tổng quát và chưa relevance
evaluation; MILP fail closed khi certificate không hợp lệ, có precision/range
guard, benchmark 25×25 không bảo đảm mọi scenario dưới 1 giây.

| Gate ngoài phần mềm | Điều kiện không thể tự tạo |
| --- | --- |
| M8 / T10.4 | Hai **người thật** làm blind review độc lập, rồi explicit adjudication/export; một reviewer vẫn pilot/pending |
| T10.2 | Người review ROI/góc nhìn trên nhiều thời điểm, reviewer/time/bằng chứng thực |
| T10.3 | Xác minh recording sessions/overlap/groups trước group-level split; unknown không được tự gán |
| T10.5 | Đo marker road-plane và vùng hiệu lực cho tốc độ metric; calibration ngưỡng cần development labels/ROI/split đủ gate |
| T10.6 | Nhãn độc lập và reviewed coverage để chấm frozen test; synthetic metrics không thay footage metrics |
| T10.7 | Host Windows NVIDIA **vật lý khác**, môi trường/log mới và người xác nhận full-process/UI |

`scripts/Test-SecondMachineHandover.ps1` đóng gói environment/code snapshot,
SHA nguồn trước/sau và `Test-Windows.ps1` (pip check, CUDA FP16/NMS, compile,
full core suite). Dùng `-ReferenceInventory` inventory gốc đã chuyển và
`-OriginMachine` hostname máy gốc; `-Manifest` mặc định `configs/cctv.demo.json`,
`-Output` tùy chọn folder mới, `-ExpectedCommit` tùy chọn pinned commit.
[Lệnh tái lập và quy trình transfer](CCTV_HANDOVER.md#kiểm-chứng-kỹ-thuật-bằng-một-lệnh).
Tooling rehearsal cục bộ đã đạt: 347 tests/1 skip, pip check/compile/CUDA
FP16/NMS và ZIP SHA; `outputs/limits-handover-verified/`. **Không** có execution
máy thứ hai; một skip do OS không cho tạo directory symlink.
Same-host rehearsal giữ `second_machine_status=NOT_RUN`; hostname khác chỉ là
`host_identity_differs`, technical pass vẫn `PENDING_HUMAN_VERIFICATION`.
`physical_host_verified=false`, `operational_ready=false`, `quality_metrics=null`,
M8 `PENDING`; không tự duyệt UI, ROI, review hoặc benchmark acceptance.


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
| T10.5 | Surveyed ground-plane calibration cho km/h; bounded threshold calibration trên development | Runner 27+9 grid hoàn tất (`pipeline/cctv/calibrate_thresholds.py`); surveyed marker N001/N002 và real labels chờ gate |
| T10.6 | Frozen test benchmark, FP/FN/ignored evidence | Chờ T10.3–T10.5 |
| T10.7 | Rebuild Windows NVIDIA vật lý khác, commit/env/log + full-process/UI human checklist | One-command tooling smoke local đạt, ZIP SHA verified; physical host NOT_RUN |
| T10.8 | Report và kịch bản bảo vệ source-backed | Báo cáo, kịch bản/Q&A và rehearsal kỹ thuật UI hoàn tất; thuyết trình người dùng/quality/second-machine pending |

Một reviewer có thể ghi quan sát/content pilot trước; không export reviewed
independent annotation. Nếu không đủ positive/negative hoặc groups độc lập,
bổ sung dữ liệu thay vì dùng fixtures hoặc đổi unknown thành negative.
M8 đã có prepare/review-notes/adjudication; không thêm workflow mới lúc này.

## Lịch sử T10.8 — báo cáo và chuẩn bị bảo vệ demo

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

## Rehearsal kỹ thuật T10.8 — đã chạy

Ngày 2026-10-01, commit `b44120a`, Windows/RTX 4070 Laptop, dùng lại dataset
M11 browser smoke; không rebuild raw/pipeline hoặc thay review. Browser chạy
T10 mới `t10-e48f46da019a4e11b710cc3879c9b354`, năm bước đạt; sau đó kiểm tra
text query (12 kết quả), source video seek 10,52/12 giây, overlay 10 giây,
5 traffic windows và ROI pending. Hai video readyState=4.

Case tổng hợp vẫn needs_more_evidence revision 1. Simulation có 1 assignment/
2 unmet; review gate có 0/1. ZIP tải qua UI chứa report JSON/Markdown với
quality_metrics=null, M8 PENDING, máy thứ hai NOT_RUN. Console không ghi lỗi;
132 protected files giữ SHA256 sau thao tác browser. Log:
`outputs/tests/t10-rehearsal.json`; worker report trong folder t10 ở trên.
Đây là rehearsal kỹ thuật tự động; buổi thuyết trình/bảo vệ vẫn chưa diễn ra.

Không còn task tự động trong phạm vi demo T10 cần triển khai. T10.1–T10.6
đánh giá độc lập chờ review/groups/ROI/nhãn, T10.7 chờ máy khác. Không đóng
các gates này bằng kết quả tổng hợp. Core suite 268 tests là log đã đạt trước
rehearsal; không chạy lại suite/inference cho cập nhật tài liệu.

## Protocol calibration/test — runner đã triển khai và kiểm chứng

Runner hiệu chỉnh ngưỡng đã được triển khai hoàn chỉnh tại `pipeline/cctv/calibrate_thresholds.py`
và CLI `scripts/calibrate_cctv_thresholds.py`, tuân thủ chính xác protocol đã chốt:

- **Congestion grid**: 27 recipes (occupancy 0,20/0,25/0,30 × motion 8/12/16 px/s × persistence 4/6/8 giây).
- **Incident grid**: 9 recipes (stationary threshold 0,02/0,04/0,06 × duration 1/2/3 giây).
- Tái sử dụng tracks đã trích xuất từ `tracks.db`, không chạy lại inference.
- Chọn riêng từng rule theo label F1 development tại IoU 0,3; hòa thì FP/h thấp hơn, khoảng cách tương đối tổng tới baseline thấp hơn, cuối cùng thứ tự tham số tăng dần.
- Khi F1 undefined (ví dụ chưa có true events hoặc chưa có match), giữ baseline recipe với lý do tường minh `baseline_retained_no_defined_f1`.
- Đánh giá test split trên bộ recipe đã chọn tại 3 ngưỡng IoU (0,1; 0,3; 0,5), xuất breakdown theo nhãn và camera.
- Xuất báo cáo đã seal `calibration_report.json` và `calibration_state.json`. Khi review intervals là pending, runner trả về trạng thái `NOT_EVALUATED_NO_REVIEWED_INTERVALS` với `operational_ready=False`, bảo toàn tính trung thực của dữ liệu.
- 5 tests tập trung trong `test_cctv_calibration.py` kiểm chứng grid size, tie-breaking, end-to-end synthetic và fail-closed khi pending.
```powershell
git diff --check
git add docs/CCTV_PROJECT_REPORT.md docs/CCTV_HANDOVER.md `
  docs/CCTV_M10_EVALUATION.md docs/CCTV_PROGRESS.md
git commit -m 'docs: prepare CCTV demo defense and UI reproduction checklist'
git push origin main
```
