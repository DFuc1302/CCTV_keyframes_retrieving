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
| Tuần 7: chất lượng/latency/phân tích lỗi | Có smoke tests, chưa có bảng benchmark | Chưa đạt |
| Tuần 8: máy thứ hai/báo cáo/bảo vệ | Hướng dẫn Windows đã có, chưa nghiệm thu máy thứ hai | Chưa đạt |

Không suy số tuần thực tế đã làm hoặc phần trăm hoàn thành từ bảng này.

## Milestones CCTV

| Milestone | Trạng thái | Bằng chứng hoặc điều kiện còn thiếu |
| --- | --- | --- |
| M0: legacy và Windows runtime | Hoàn tất nền | `.venv`, CUDA FP16/NMS, requirements và kiểm kê; full legacy suite vẫn có vấn đề được ghi riêng |
| M1: manifest và extraction | Hoàn tất demo | 6 video, 2 camera N001/N002, 7.232 frames; raw chỉ đọc, resume |
| M2: retrieval và demo API/UI | Hoàn tất chức năng | CLIP 768D, FAISS, SQLite read-only, text/image, video seek, readiness thật |
| M3: xe, tracking, congestion | Baseline demo có | T3.1–T3.5 chạy thử, ROI/timeline đã có trên API/UI; review ROI, hiệu chỉnh và benchmark còn mở |
| M4: tình huống bất thường/nghi tai nạn | T4.1 có nền evidence/review | Package ứng viên và CLI review đã có; T4.2 quy tắc nghi sự cố và nhãn độc lập còn mở |
| M5: gợi ý phân bổ nhân lực | Chưa bắt đầu | Vị trí camera/khu vực, nguồn lực, ràng buộc và dữ liệu mô phỏng/được xác nhận |
| M6: đánh giá và bàn giao | Chưa hoàn tất | Qrels/splits, benchmark retrieval/events, latency, báo cáo, máy thứ hai |

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
Chi tiết và bằng chứng cục bộ: `CCTV_DEMO_UI.md`, `CCTV_RETRIEVAL.md`,
`outputs/demo-data-review/ui-verification.json`.

## Task tiếp theo và các bước có thể commit riêng

**T3.1–T3.5 baseline đã hoàn tất trong phạm vi được ghi; ROI thật vẫn chưa
duyệt và ngưỡng chưa hiệu chỉnh. T4.1 evidence/review đã hoàn tất trong phạm vi
CLI. Task tiếp theo: T4.2 — quy tắc nghi sự cố, fixtures và kế hoạch đánh giá.** Xem `CCTV_TRAFFIC.md` để
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
| T3.5: API/UI giao thông — đã có baseline | ROI tham chiếu, chỉ số, timeline và mở evidence video; traffic readiness riêng và provenance/hash guards; chưa có overlay tracking video trực tiếp |
| T4.1: event evidence/review — đã có nền CLI | Schema ứng viên/demo, frame/PTS thật, package immutable, video checkpoint, journal review riêng và CAS; 0 ứng viên dữ liệu hiện có, 1 case tổng hợp để test |
| T4.2: nghi sự cố — tiếp theo | Mở rộng schema/rules theo thời gian, fixtures positive/negative; thử trên dữ liệu có nhãn độc lập và đo false alarms trước khi tuyên bố nhận diện tai nạn |
| T5.1–T5.2: nguồn lực | Cấu hình khu vực/đội tuần tra và bộ gợi ý có lý do; kiểm thử ngân sách, năng lực và ràng buộc bằng dữ liệu mô phỏng rõ nhãn |
| T6.1–T6.3: benchmark/bàn giao | Tạo nhãn và split song song từ M3; đánh giá cấu hình đóng băng, báo cáo và chạy lại máy thứ hai |

Mỗi task: code/module hoàn chỉnh → kiểm thử phù hợp và dữ liệu mẫu → tài liệu
PowerShell → Conventional Commit → push. Chạy CLIP phục vụ và detector theo
stage riêng để tránh giữ hai model lớn đồng thời trên GPU 8GB.

Hiện chưa có nhãn tai nạn/tắc đường, ROI, vị trí camera hoặc nguồn lực thực tế.
Các thiếu hụt này không cản việc tạo schema và detector baseline, nhưng chưa đủ
để nghiệm thu chất lượng sự kiện hoặc khuyến nghị điều phối thực tế.
