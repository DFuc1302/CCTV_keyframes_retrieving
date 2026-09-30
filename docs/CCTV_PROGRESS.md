# Đối chiếu kế hoạch và tiến độ CCTV

Ngày đối chiếu: 2026-09-30. Mốc code: `55f06fa`, đã push `origin/main`.

## Cách đọc kế hoạch

`KE_THUA_VA_PHAT_TRIEN_DO_AN.md`, mục 9, là lộ trình tám tuần cho video bài giảng.
Các yêu cầu môn học/OCR/ASR trong đó chưa phải checklist nghiệm thu CCTV.
Định hướng CCTV của người dùng thay thế phạm vi bài giảng, giữ kiến trúc kế thừa.
M0/M1/M2 đã được dùng trong tài liệu repo; M3–M6 và mã task dưới đây là kế hoạch
tiếp nối được đề xuất trong lần đối chiếu này, chưa phải module đã triển khai.

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
| M3: xe, tracking, congestion | Đang thực hiện | T3.1 schema/ROI config đã có; ROI camera chưa duyệt, detector/tracking/congestion chưa chạy |
| M4: tình huống bất thường/nghi tai nạn | Chưa bắt đầu | Quy tắc theo thời gian, evidence clips, review và nhãn độc lập |
| M5: gợi ý phân bổ nhân lực | Chưa bắt đầu | Vị trí camera/khu vực, nguồn lực, ràng buộc và dữ liệu mô phỏng/được xác nhận |
| M6: đánh giá và bàn giao | Chưa hoàn tất | Qrels/splits, benchmark retrieval/events, latency, báo cáo, máy thứ hai |

Sau hai bản sửa launcher (encoding PowerShell và cổng bận), core suite đã chạy
lại bằng Windows PowerShell 5.1: **47 tests đạt**, runtime CUDA FP16/NMS đạt.
Có regression tests parser trên đĩa và kiểm tra cổng bận trước inference.
Kết quả này không đồng nghĩa full legacy suite đạt.
Sau T3.1, core suite đã chạy lại: **55 tests đạt** (thêm 8 tests traffic),
cùng runtime Windows CUDA FP16/NMS. CLI demo trả schema_valid=true,
roi_ready=false, pending N001/N002 như dự kiến.
Chi tiết và bằng chứng cục bộ: `CCTV_DEMO_UI.md`, `CCTV_RETRIEVAL.md`,
`outputs/demo-data-review/ui-verification.json`.

## Task tiếp theo và các bước có thể commit riêng

**T3.1 đã hoàn tất phần schema và cấu hình; ROI thật vẫn chưa duyệt. Task tiếp
theo: T3.2 — pipeline phát hiện xe baseline.** Xem `CCTV_TRAFFIC.md` để kiểm tra
ROI readiness; detection baseline có thể chạy trước khi ROI được duyệt.
Chưa sửa model/index CLIP hiện có. Tạo schema/output độc lập cho detections,
tracks và traffic windows, giữ camera/video/timestamp/frame_idx để liên kết
keyframe. ROI đường dùng tọa độ chuẩn hóa và cần duyệt trên ảnh từng camera;
không tự suy vị trí, thời gian tuyệt đối hoặc hiệu chuẩn tốc độ từ tên video.

| Task đề xuất | Kết quả nhỏ có thể kiểm thử và push |
| --- | --- |
| T3.1: schema/ROI — đã có | Validate ROI và locator; kiểm thử cấu hình hợp lệ/lỗi; N001/N002 chưa duyệt |
| T3.2: phát hiện xe | Pipeline inference streaming, FP16, batch nhỏ, model/checkpoint cố định; preview bounding boxes, resume và đo VRAM trên clip nhỏ |
| T3.3: tracking | Track trên các frame liên tiếp lấy từ video gốc vào output riêng; giữ PTS thực, kiểm thử reset/gián đoạn; không lấy index keyframes 2 FPS làm bằng chứng tracking đủ tốt |
| T3.4: congestion | Occupancy/chuyển động ảnh theo cửa sổ thời gian, persistence; ngưỡng hiệu chỉnh theo camera với nhãn; không gọi pixel displacement là km/h |
| T3.5: API/UI giao thông | Overlay xe/ROI, timeline và evidence; kiểm thử readiness và liên kết timestamp |
| T4.1–T4.2: nghi sự cố | Event schema, evidence và human review; thử trên dữ liệu có nhãn sự cố, đo false alarms trước khi tuyên bố nhận diện tai nạn |
| T5.1–T5.2: nguồn lực | Cấu hình khu vực/đội tuần tra và bộ gợi ý có lý do; kiểm thử ngân sách, năng lực và ràng buộc bằng dữ liệu mô phỏng rõ nhãn |
| T6.1–T6.3: benchmark/bàn giao | Tạo nhãn và split song song từ M3; đánh giá cấu hình đóng băng, báo cáo và chạy lại máy thứ hai |

Mỗi task: code/module hoàn chỉnh → kiểm thử phù hợp và dữ liệu mẫu → tài liệu
PowerShell → Conventional Commit → push. Chạy CLIP phục vụ và detector theo
stage riêng để tránh giữ hai model lớn đồng thời trên GPU 8GB.

Hiện chưa có nhãn tai nạn/tắc đường, ROI, vị trí camera hoặc nguồn lực thực tế.
Các thiếu hụt này không cản việc tạo schema và detector baseline, nhưng chưa đủ
để nghiệm thu chất lượng sự kiện hoặc khuyến nghị điều phối thực tế.
