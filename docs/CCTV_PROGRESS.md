# Đối chiếu kế hoạch và tiến độ CCTV

Ngày cập nhật: 2026-10-01. T3.5 tiếp nối traffic windows tại `8fdba1a`;
commit triển khai được ghi trong Git history.

## Cách đọc kế hoạch

`KE_THUA_VA_PHAT_TRIEN_DO_AN.md`, mục 9, là lộ trình tám tuần cho video bài giảng.
Các yêu cầu môn học/OCR/ASR trong đó chưa phải checklist nghiệm thu CCTV.
Định hướng CCTV của người dùng thay thế phạm vi bài giảng, giữ kiến trúc kế thừa.
M0/M1/M2 đã được dùng trong tài liệu repo; M3–M6 và mã task dưới đây là kế hoạch
tiếp nối; trạng thái triển khai được cập nhật trong bảng dưới đây.

## Đối chiếu lộ trình gốc

| Mốc gốc | Bằng chứng hiện tại | Trạng thái trong phạm vi CCTV |
| --- | --- | --- |
| Tuần 1: tiếp nhận, kiểm kê, readiness | M0 inventory, runtime Windows, `/ready` của API CCTV 200 | Đạt cho API CCTV mới; serving bundle/API legacy chưa được nghiệm thu lại |
| Tuần 2: video mới qua pipeline | 6 MOV, 7.232 keyframes/maps, CLIP/FAISS/SQLite | Đạt nhánh hình ảnh; chưa chạy OCR/ASR trên CCTV |
| Tuần 3: chuẩn hóa pipeline | Manifest độc lập, output riêng, checkpoint/resume, hash guard | Đạt phạm vi pipeline CCTV hiện tại |
| Tuần 4: UI và vị trí video | Text/image query, xem MP4 tại timestamp, responsive | Đạt demo CCTV, không phải UI môn học |
| Tuần 5: cải tiến có baseline so sánh | Chưa có thực nghiệm so sánh | Chưa đạt |
| Tuần 6: benchmark có nhãn và split | Demo chưa có relevance/event labels độc lập | Chưa đạt |
| Tuần 7: chất lượng/latency/phân tích lỗi | Có bảng latency HTTP T6.2; chất lượng thiếu nhãn | Đạt phép đo kỹ thuật cục bộ; chất lượng chưa nghiệm thu |
| Tuần 8: máy thứ hai/báo cáo/bảo vệ | Hướng dẫn Windows đã có, chưa nghiệm thu máy thứ hai | Chưa đạt |

Không suy số tuần thực tế đã làm hoặc phần trăm hoàn thành từ bảng này.

## Milestones CCTV

| Milestone | Trạng thái | Bằng chứng hoặc điều kiện còn thiếu |
| --- | --- | --- |
| M0: legacy và Windows runtime | Hoàn tất nền | `.venv`, CUDA FP16/NMS, requirements và kiểm kê; full legacy suite vẫn có vấn đề được ghi riêng |
| M1: manifest và extraction | Hoàn tất demo | 6 video, 2 camera N001/N002, 7.232 frames; raw chỉ đọc, resume |
| M2: retrieval và demo API/UI | Hoàn tất chức năng | CLIP 768D, FAISS, SQLite read-only, text/image, video seek, readiness thật |
| M3: xe, tracking, congestion | Baseline demo có | T3.1–T3.5 chạy thử, ROI/timeline đã có trên API/UI; review ROI, hiệu chỉnh và benchmark còn mở |
| M4: tình huống bất thường/nghi tai nạn | T4.1–T4.2 có baseline CLI | Evidence/review và move-then-stop rule chạy thử; chưa có nhãn độc lập, calibration hoặc nhận diện tai nạn |
| M5: gợi ý phân bổ nhân lực | T5.1–T5.2 có baseline mô phỏng | Schema và solver bounded exact có assignments/unmet reasons; T7.2 bổ sung UI chỉ đọc, chưa có vị trí/nguồn lực thật |
| M6: đánh giá và bàn giao | T6.1–T6.3 có công cụ/tài liệu | Event evaluator, frozen HTTP latency, báo cáo và package bàn giao đã có; nhãn thật và chạy máy thứ hai còn mở |
| M7: tích hợp demo sự kiện/điều phối | T7.1–T7.3 đạt kỹ thuật cục bộ | Event/evidence, phân bổ mô phỏng, HTTP/browser smoke và runtime mới có; chưa nghiệm thu chất lượng hoặc máy thứ hai |
| M8: chuẩn bị review/nhãn độc lập | T8.1–T8.3 có công cụ pending | Tư liệu sạch, notes riêng, comparison và explicit adjudication/export; chưa review thật hoặc tạo ground truth |
| M9: tiện ích chạy thử | Hoàn tất T9.1–T9.6 | HTTP smoke, tracking overlay API/UI, runtime và rebuild đã kiểm chứng cục bộ; chất lượng/nhãn thật thuộc các mốc còn mở |
| M10: đánh giá độc lập và tái lập | Đang chuẩn bị T10.1 | Inventory sáu video và forms pending đã có; chỉ một reviewer, M8 pending theo user; ROI/groups/labels/máy thứ hai chưa đủ đầu vào |

Sau hai bản sửa launcher (encoding PowerShell và cổng bận), core suite đã chạy
lại bằng Windows PowerShell 5.1: **47 tests đạt**, runtime CUDA FP16/NMS đạt.
Có regression tests parser trên đĩa và kiểm tra cổng bận trước inference.
Kết quả này không đồng nghĩa full legacy suite đạt.
Sau T3.1, core suite đã chạy lại: **55 tests đạt** (thêm 8 tests traffic),
cùng runtime Windows CUDA FP16/NMS. CLI demo trả schema_valid=true,
roi_ready=false, pending N001/N002 như dự kiến.
Sau T3.2: **63 core tests đạt**, runtime CUDA FP16/NMS đạt. Detector đã xử lý đủ
7.232 keyframes và resume skip đủ, không nạp model/không rewrite artifacts.
Xem `CCTV_DETECTION.md`; chưa có benchmark detector hoặc sự kiện.
Sau T3.3: **74 core tests đạt**, runtime CUDA FP16/NMS đạt. Tracking từ video
gốc chạy 6 clip 10 giây, 1.501 frame/12 segment; join 120 timestamp keyframe
cũ, resume skip đủ không nạp model và không rewrite 18 artifacts/state/log.
Xem `CCTV_TRACKING.md`; chưa nghiệm thu tracking toàn video hoặc chất lượng ID.
Sau T3.4: **92 core tests đạt**, runtime CUDA FP16/NMS đạt. Chế độ draft chạy
30 cửa sổ từ 6 clip/1.501 frame, resume skip đủ và không rewrite 6 artifacts/
state/log. Có 6 ảnh ROI nháp, ROI vẫn pending và ngưỡng chưa hiệu chỉnh.
Xem `CCTV_CONGESTION.md`; không coi kết quả demo là benchmark congestion.
Sau T3.5: **100 core tests đạt**, CUDA FP16/NMS đạt. API thật phục vụ 30 windows/
6 ROI images; trình duyệt lọc camera/video, chọn timeline và seek 34.001 giây
đến frame 850 đúng N002-V003. Mobile 390×844 không tràn ngang toàn trang.
Traffic readiness độc lập retrieval, giữ cờ draft/uncalibrated. Xem `CCTV_TRAFFIC_UI.md`.
Sau T4.1: **116 core tests đạt**, CUDA FP16/NMS đạt. Package sáu clip có
0 ứng viên từ traffic rule; package tổng hợp riêng có 1 `demo_case`, tham
chiếu frame thật N001-V001 751/30.037 giây và 900/35.996 giây. CLI review
kiểm thử ba revision trong journal demo, giữ scope demo và flags gốc.
Evidence/schema/hash, checkpoint/resume, bảo vệ input, CAS, reopen và audit
chain đã kiểm thử. Chưa có nhận diện tai nạn hoặc event UI. Xem `CCTV_EVENTS.md`.
Sau T4.2: **128 core tests đạt**, CUDA FP16/NMS đạt. Move-then-stop screening
chạy CPU trên 6 clip/1.501 frame, tạo 0 ứng viên và resume giữ nguyên output.
10 quỹ đạo tổng hợp đạt kỳ vọng rule, gồm chờ đèn đỏ cũng tạo ứng viên để
minh họa false alarm. Event v2 giữ scope track/segment và evidence/PTS; review
v1/v2 tương thích, raw và mọi upstream publications giữ nguyên. Xem
`CCTV_INCIDENTS.md`, kế hoạch nhãn/split/metrics trong `CCTV_EVENT_EVALUATION.md`.
Sau T5.1: **141 core tests đạt**, CUDA FP16/NMS đạt. Resource simulation có
2 zone/3 team, roster=6, available=4, reserve=2 nên dispatchable cap=2.
Schema kiểm tra canonical camera bindings, unknown=null, directed travel,
team skills/availability/allowed zones, budget và indivisible teams. Report
resume giữ nguyên hash/mtime; raw và mọi upstream artifacts giữ nguyên.
Chưa tạo assignments hoặc gợi ý điều động. Xem `CCTV_RESOURCES.md`.
Sau T5.2: **155 core tests đạt**, CUDA FP16/NMS đạt. Exact simulation solver
gán SIM-TEAM-A (2 người) cho demand priority 4, giữ reserve=2; hai demand còn
lại bị chặn bởi review gate/shared cap. Brute-force oracle độc lập kiểm tra
objective; cap, whole teams, skills, directed travel và mỗi team một lần đã
kiểm thử. Event package rỗng không tạo assignment; demo case needs_more_evidence
được giữ lại, journal không sửa. Ba package resume giữ nguyên bytes/mtime,
raw và upstream artifacts giữ nguyên. Xem `CCTV_DISPATCH.md`.
Sau T6.1: **171 core tests đạt**, CUDA FP16/NMS đạt. Annotation validator,
video/recording-group splits và camera holdout checks; max-total-IoU exact
matching và sensitivity 0.1/0.3/0.5, TP/FP/FN/precision/recall/F1, reviewed
camera-hours và confirmation-PTS delay đã có. Fixture riêng TP=1/FP=2/FN=1
đối chiếu oracle. Sáu clip thật giữ pending/unassigned; CLI trả
NOT_EVALUATED_NO_REVIEWED_INTERVALS, metrics=null. Resume giữ bytes/mtime,
không đổi nhãn/raw/upstream packages. Xem `CCTV_BENCHMARK.md`.
Chi tiết và bằng chứng cục bộ: `CCTV_DEMO_UI.md`, `CCTV_RETRIEVAL.md`,
`outputs/demo-data-review/ui-verification.json`.

Sau T6.2: **182 core tests đạt**, CUDA FP16/NMS và pip check đạt. Workload
12 query, top-k=12, 24 warmup/120 request đo qua HTTP; lỗi=0. Text P50/P95
52,66/56,43 ms; image 127,91/141,34 ms. Client không tải model, server một
CLIP FP16; allocator peak trong phiên model allocated=843,79 MiB,
reserved=862 MiB, không phải tổng VRAM hoặc peak riêng request. Frozen input,
index/model/code/session guards, case checkpoint và no-network completed
resume đã kiểm tra. Raw 6 file, nhãn pending và upstream artifacts giữ nguyên
hash/mtime. Chưa có relevance labels; quality metrics=null. Xem
`CCTV_RUNTIME_BENCHMARK.md`, `outputs/demo-data/runtime-benchmark-v1/runtime_report.json`
và `outputs/demo-data-review/runtime-benchmark-verification.json`.

Sau T6.3: **189 core tests đạt**, CUDA FP16/NMS và pip check đạt. Báo cáo
tổng hợp `CCTV_PROJECT_REPORT.md`, quy trình rebuild theo stage bằng PowerShell
và exporter `scripts/export_cctv_handover.py` đã có. Package
`outputs/demo-data/handover-v1` đối chiếu ba sealed reports, source manifest,
checksum/protected inputs; resume giữ hash/mtime ba JSON. Template máy thứ
hai có 11 checks pending, trạng thái NOT_RUN; không sao chép raw/model hoặc
tự tạo kết quả PASS. Chất lượng retrieval/event vẫn chưa có nhãn độc lập.
Xem `CCTV_HANDOVER.md` và `outputs/demo-data-review/handover-verification.json`.

Sau T7.1: **194 core tests đạt**, CUDA FP16/NMS và pip check đạt. API event
v1/v2 chỉ đọc, source/ledger/journal binding, live review state/revision và
video evidence seek đã có. Tab phân biệt package unavailable, 0 candidates
và case synthetic. Không ghi review, không nạp model thứ hai. Benchmark T6.2
giữ như phép đo lịch sử, code mới cần output đo mới. Xem `CCTV_EVENTS_UI.md`;
dispatch UI tiếp nối T7.2.
Browser mở evidence đúng 30,037/35,996 giây, giữ review revision 3; raw,
event/journal và upstream artifacts giữ hash/mtime. Bằng chứng:
`outputs/demo-data-review/events-api-verification.json`.

Sau T7.2: **202 core tests đạt**, pip check, compile và CUDA FP16/NMS đạt.
API/UI chỉ đọc ba báo cáo mô phỏng: fixture 1 assignment/2 unmet; event rỗng
0/0; demo review gate 0/1, `needs_more_evidence` revision 3. Kiểm tra hiện tại
nguồn/config/journal, cap/reserve và khả thi; snapshot stale trả unavailable,
không chạy solver hoặc nạp model khác. HTTP kiểm tra 68 artifacts/configs giữ
hash/mtime; browser ba kịch bản, Home/End và console không lỗi. Xem
`CCTV_DISPATCH_UI.md` và `outputs/demo-data-review/dispatch-ui-verification.json`.
Benchmark T6.2 vẫn là lịch sử; T7.3 đo lại vào output mới với code hiện tại.

Sau T7.3: **208 core tests đạt**, pip check, compile và CUDA FP16/NMS đạt.
CLI `scripts/verify_cctv_demo.py` kiểm tra UI assets, text/image locator, ảnh,
video Range, 30 windows, events và ba dispatch packages, không ghi review.
Browser kiểm tra search video tại 488,014 giây, traffic evidence 30,037 giây,
event evidence 35,996 giây, review gate và console không lỗi. Benchmark mới:
120 request/24 warmup, lỗi=0; text P50/P95=49,76/53,78 ms, image
147,91/167,64 ms. Hai report/state giữ hash/mtime khi resume. Xem
`CCTV_DEMO_ACCEPTANCE.md`; report local dưới `acceptance-t7-v2` và
`runtime-benchmark-t7-v1`. Chất lượng, ROI/nhãn và máy thứ hai còn mở.

Sau T8.1: **214 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt.
Bundle `review-preparation-v1` có sáu khoảng `[30,40)`, 36 JPEG nguyên bản,
sáu contact sheets và sáu ROI overlays riêng. Trang labeling không có ROI
draft/predictions, dùng video preview hiện có/source PTS; forms giữ pending,
0 reviewed/approved, group unverified và split unassigned. Resume skip sáu
interval, 55 artifacts giữ SHA/mtime, 59 input files được freeze. Browser
tải sáu sheets, video seek 30,000 giây và console không lỗi; sáu raw cùng
upstream không đổi. Bộ này không chứng minh blind review/tính đại diện.
Xem `CCTV_REVIEW_PREPARATION.md`; notes/nhãn thật chưa được cung cấp.

## Task tiếp theo và các bước có thể commit riêng

**T3.1–T3.5 baseline đã hoàn tất trong phạm vi được ghi; ROI thật vẫn chưa
duyệt và ngưỡng chưa hiệu chỉnh. T4.1 evidence/review đã hoàn tất trong phạm vi
CLI. T4.2 có baseline rule/fixtures và evaluation protocol; chưa nghiệm thu
nhận diện tai nạn. T5.1 đã có schema khu vực/nguồn lực mô phỏng.
T5.2 đã có bộ gợi ý bounded exact bằng CLI, toàn bộ nguồn lực vẫn mô phỏng.
T6.1 evaluator và T6.2 frozen runtime benchmark đã có; nhãn độc lập và
recording grouping vẫn chưa xác nhận.
T6.3 đã có gói bàn giao/báo cáo và checklist. Bước nghiệm thu tiếp theo:
chạy trên máy thứ hai theo `CCTV_HANDOVER.md`, lưu commit/log/kết quả thực;
review ROI và tạo nhãn độc lập vẫn còn mở. T7.1 đã có UI event/evidence chỉ
đọc; T7.2 đã nối phân bổ mô phỏng lên UI và T7.3 nghiệm thu kỹ thuật cục bộ.
T8.1 đã chuẩn bị bộ mẫu pending, T8.2 có notes riêng và validation, T8.3
có quyết định adjudication/export sang schema T6.1. Bước nghiệm thu tiếp
theo cần review/adjudication thực, xác minh groups/splits và ROI; không tự
phê duyệt hoặc tạo nhãn thật. Chạy máy thứ hai vẫn cần môi trường
thứ hai thực tế.** Xem `CCTV_TRAFFIC.md` để
kiểm tra ROI readiness. Tracking cần frame liên tiếp và PTS thật; detections
keyframes 2 FPS hiện tại chưa chứng minh tracking hoặc lưu lượng hoạt động tốt.
Chưa sửa model/index CLIP hiện có. Tạo schema/output độc lập cho detections,
tracks và traffic windows, giữ camera/video/timestamp/frame_idx để liên kết
keyframe. ROI đường dùng tọa độ chuẩn hóa và cần duyệt trên ảnh từng camera;
không tự suy vị trí, thời gian tuyệt đối hoặc hiệu chuẩn tốc độ từ tên video.

| Task đề xuất | Kết quả nhỏ có thể kiểm thử và push |
| --- | --- |
| T3.1: schema/ROI — đã có | Validate ROI và locator; kiểm thử cấu hình hợp lệ/lỗi; N001/N002 chưa duyệt |
| T3.2: phát hiện xe — đã có | FP16, batch giới hạn, checkpoint và sáu previews; đủ 7.232 frames, resume/hash/VRAM đã kiểm tra |
| T3.3: tracking — đã có baseline clip | Frame liên tiếp/PTS từ video gốc, ID theo scope segment, checkpoint/replay, 12 previews; chưa có nhãn hoặc benchmark tracking |
| T3.4: congestion — đã có baseline demo | Count/union occupancy, pixel motion/PTS, cửa sổ có coverage và persistence; draft mode rõ ràng, ROI review và hiệu chỉnh ngưỡng bằng nhãn còn mở |
| T3.5: API/UI giao thông — đã có baseline | ROI tham chiếu, chỉ số, timeline và evidence; T9.3 bổ sung overlay tracking trực tiếp với provenance/offset riêng |
| T4.1: event evidence/review — đã có nền CLI | Schema ứng viên/demo, frame/PTS thật, package immutable, video checkpoint, journal review riêng và CAS; 0 ứng viên dữ liệu hiện có, 1 case tổng hợp để test |
| T4.2: nghi sự cố — đã có baseline | Move-then-stop theo scoped tracks/PTS, schema v2, 10 fixtures và protocol nhãn/split/false alarms; benchmark thật và nhận diện tai nạn còn mở |
| T5.1: nguồn lực — đã có cấu hình mô phỏng | Zone/team/skills/allowed zones, availability, directed travel, budget/reserve; schema và report/resume chạy thử, không gán vị trí thật |
| T5.2: gợi ý phân bổ — đã có baseline CLI | Exact priority/travel/staff objective trong giới hạn 12 team/demand; cap/reserve/skills và unmet reasons, v1/v2 review gate, giữ flags demo; không điều động thực tế |
| T6.1: annotation/splits/evaluator — đã có công cụ | Source-bound labels, review/coverage validation, group/camera leakage checks, exact weighted IoU matching, metrics và fixtures; chưa có nhãn thật để chấm chất lượng |
| T6.2: runtime benchmark — đã có kỹ thuật | Frozen workload/input/model/index/code, 120 HTTP samples, P50/P95 và lỗi, case checkpoint; relevance/event quality thiếu nhãn |
| T6.3: bàn giao — đã chuẩn bị | Báo cáo, exporter checksum/resume và quy trình rebuild/checklist; máy thứ hai NOT_RUN, benchmark nhãn thật vẫn cần thực hiện |
| T7.1: event UI — đã có chỉ đọc | Event/review state, source guards, synthetic/empty/unavailable states và video evidence; không ghi review |
| T7.2: phân bổ UI — đã có chỉ đọc | Đọc simulation report, cap/reserve, assignments/unmet reasons; cấu hình/journal đổi thì snapshot cũ không khả dụng |
| T7.3: nghiệm thu demo — đạt kỹ thuật cục bộ | HTTP/browser smoke và latency mới đã có; giữ benchmark lịch sử, chưa có nhãn thật/máy thứ hai |
| T8.1: chuẩn bị review — đã có | 36 ảnh sạch, sáu sheets/ROI drafts và forms pending; checkpoint/resume, không đọc predictions |
| T8.2: notes/validation — đã có | Hai templates riêng, scope/time/provenance và exact multiset comparison; sáu khoảng demo vẫn pending, không xuất ground truth |
| T8.3: adjudication/export — đã có | Quyết định rõ ràng, notes SHA/coverage/blind/acceptance/time gates, chuyển schema T6.1; demo vẫn sáu khoảng pending |
| T9.1: managed demo smoke — đã có | Một lệnh PowerShell, socket reservation, readiness timeout, HTTP T7.3 và shutdown server sở hữu; existing mode không tạo/dừng server |
| T9.2: tracking overlay — đã có offline | MP4 H.264 CPU, bbox/segment-scoped ID từ ledger, source/PTS guards, checkpoint/resume; không inference mới hoặc duyệt ROI |
| T9.3: overlay trên traffic UI — đã có | MP4/Range và optional catalog, provenance/snapshot guards, source offset, nút xem clip; không duyệt dữ liệu |
| T9.4: runtime/bàn giao hiện tại — đã có | 120 measured + 24 warmup không lỗi, resume không HTTP, sealed handover; máy thứ hai NOT_RUN |
| T9.5: hoàn thiện hướng dẫn rebuild — đã có | Bổ sung ROI previews, events, overlay và dispatch snapshots; kiểm chứng CLI/resume và pending review không gán đội |
| T9.6: smoke cho bundle dựng mới — đã có | Bỏ phụ thuộc runtime lịch sử/journal bắt buộc, bảo vệ request và journal có/không có; regression và HTTP smoke đạt |

Sau T8.2: **225 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt.
11 tests mới dùng fixtures; hai templates và comparison demo giữ sáu khoảng
pending, không tạo ground truth. Hướng dẫn: `CCTV_REVIEW_NOTES.md`.

Sau T8.3: **235 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt.
10 tests mới kiểm tra adjudication bằng fixtures. Export demo giữ sáu khoảng
pending; evaluator T6.1 đọc được và trả NOT_EVALUATED_NO_REVIEWED_INTERVALS,
metrics null. Resume giữ SHA/mtime 69 files nguồn/tư liệu/artifacts kiểm tra,
sáu video raw và upstream packages không đổi. Hướng dẫn: `CCTV_ADJUDICATION.md`.

T9.1 bổ sung tiện ích theo mục tiêu chạy thử hiện tại: **244 core tests đạt**,
pip check/compile/CUDA FP16/NMS đạt; managed HTTP smoke trên 8087 passed,
100 protected files giữ SHA/mtime và socket giải phóng sau shutdown.
Xem `CCTV_DEMO_SMOKE.md`; nhãn/ROI/máy thứ hai vẫn chưa nghiệm thu.

T9.2: **252 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt; sáu MP4
overlay CPU có 1.501 frames, PTS output đối chiếu đầy đủ. Resume skip sáu
video, 26 files giữ SHA/mtime; raw/tracking/upstream giữ nguyên.
Xem `CCTV_TRACKING_OVERLAY.md`. T9.3 đã nối overlay vào UI traffic:
**259 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt; HTTP smoke
RequireOverlays đạt sáu clips, 108 protected files giữ SHA/mtime. Browser
source 36,036 → overlay 5,999 giây, readyState=4, console không lỗi.
Xem `CCTV_OVERLAY_UI.md`; ROI/nhãn thật và máy thứ hai vẫn pending.

T9.4: benchmark app `9ff3006` đạt; text P50/P95=52,18/56,18 ms,
image=128,07/139,86 ms, không lỗi trong 120 measured + 24 warmup requests.
Resume không HTTP; 56 protected files và hai runtime JSON giữ SHA/mtime.
Gói `handover-t9-v1` export/resume ba JSON đạt; máy thứ hai NOT_RUN,
event metrics null. Không đổi executable code, giữ bằng chứng 259 tests
T9.3; xem `CCTV_RUNTIME_BENCHMARK.md` và `CCTV_HANDOVER.md`.

T9.5 sửa hướng dẫn handover/README còn thiếu assets hoặc mô tả UI cũ.
Sáu CLI commands resume đạt, 76 protected files giữ SHA/mtime. Rehearsal
CPU riêng với journal chưa có giữ pending_review: 0 assignments/1 unmet,
không tạo journal. Không đổi executable code, không nạp model; máy thứ hai
vẫn NOT_RUN. Xem `CCTV_HANDOVER.md`, mục T9.5.

T9.6 sửa dependency không cần thiết trong verifier dùng chung cho CLI và
managed/existing smoke. Không cần report runtime lịch sử hoặc journal đã
review; journal absent phải giữ absent trong run, request dispatch được
freeze. Regression kiểm tra thiếu cả ba journals/report và reject journal
xuất hiện giữa run. Managed CUDA smoke đạt sáu overlays/30 windows,
107 protected files giữ SHA/mtime, owned server dừng và cổng 8087 giải phóng.
Report: `outputs/demo-data/demo-smoke-20261001-171142-10994a76/acceptance_report.json`.
Không sửa raw/review/ROI hoặc logic inference/dispatch. Các mốc còn cần
đầu vào thực vẫn là review/labels/groups/ROI và máy thứ hai; không bổ sung
module mới chỉ để tăng số task.
Sau T9.6: **260 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt.

## Đóng milestone M9 — 2026-10-01

Toàn bộ task T9 đã hoàn tất trong phạm vi chạy thử demo cục bộ:

| Task | Commit triển khai | Kết quả nghiệm thu |
| --- | --- | --- |
| T9.1 | `23ffa86` | Managed HTTP smoke và shutdown server sở hữu |
| T9.2 | `6efa11a` | Sáu MP4 overlay CPU, 1.501 frames/PTS verify và resume |
| T9.3 | `9ff3006` | Overlay API/Range, traffic UI và browser source-offset playback |
| T9.4 | `6138cfb` | 120 measured + 24 warmup không lỗi, runtime/handover resume |
| T9.5 | `ecb9cc1` | Hướng dẫn rebuild assets, CLI/PowerShell và pending dispatch |
| T9.6 | `ba35139` | Smoke không cần journals/report lịch sử; 260 core tests đạt |

Đối chiếu cuối: report smoke T9.6 passed, sáu overlays/30 windows;
SHA256 của 107 protected files trong report vẫn khớp files hiện tại.
Không thay code sau bộ kiểm thử này; không cần thêm task T9 hoặc chạy lại
inference để đóng milestone. Lệnh chạy lại demo:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Test-CctvDemo.ps1 -RequireOverlays
```

M9 complete không đồng nghĩa toàn bộ đồ án đã nghiệm thu: các điều kiện
review/labels/groups/ROI, chất lượng retrieval/events và máy thứ hai vẫn
thuộc M3/M4/M6/M8; vị trí/nguồn lực thật của M5 chưa có. Giữ pending,
không tự tạo phê duyệt hoặc kết quả thực nghiệm để đóng các mốc đó.

```powershell
git diff --check
git add docs/CCTV_PROGRESS.md
git commit -m 'docs: close T9 demo milestone with verified acceptance evidence'
git push origin main
```

## M10 — triển khai theo kế hoạch đánh giá

Chi tiết protocol/tasks/gates và PowerShell: `CCTV_M10_EVALUATION.md`.
T10.1 technical inventory đã chạy trên sáu MOV hiện có, 3.614,160 giây
container duration, same raw SHA như inventory gốc. Năm JSON resume giữ
SHA/mtime; content/groups/splits/ROI không tự suy hoặc duyệt. Kiểm kê kỹ
thuật hoàn tất không đồng nghĩa content/group verification hoàn tất.
Theo cập nhật người dùng: chỉ một reviewer, giữ M8 pending và chờ thông tin.
T10.2–T10.6 chờ ROI/groups/nhãn đủ gate; không chạy calibration trên demo
pending. T10.7 chưa có máy thứ hai được truy cập/chạy; vẫn NOT_RUN.
Kiểm chứng bước chuẩn bị: **261 core tests đạt**, pip check/compile/CUDA
FP16/NMS đạt; bản sao ba forms working đã tạo mà không phê duyệt hoặc gán nhãn.

Mỗi task: code/module hoàn chỉnh → kiểm thử phù hợp và dữ liệu mẫu → tài liệu
PowerShell → Conventional Commit → push. Chạy CLIP phục vụ và detector theo
stage riêng để tránh giữ hai model lớn đồng thời trên GPU 8GB.

Hiện chưa có nhãn tai nạn/tắc đường, ROI, vị trí camera hoặc nguồn lực thực tế.
Các thiếu hụt này không cản việc tạo schema và detector baseline, nhưng chưa đủ
để nghiệm thu chất lượng sự kiện hoặc khuyến nghị điều phối thực tế.
