# 📚 Bản Đồ Tài Liệu Kỹ Thuật (Documentation Hub)
**Hệ Thống CCTV Keyframe Retrieval, Traffic Analysis, Incident Detection & Police Dispatch**  
*Dự án VGU Explore Computer Science | Môi trường: Windows 11 Native / WSL2 Ubuntu, Python 3.11, CUDA 12.6, RTX 4070 Laptop*

---

Thư mục `docs/` chứa toàn bộ tài liệu kiến trúc, đặc tả phân hệ kỹ thuật, hướng dẫn triển khai và báo cáo thực nghiệm của dự án. Tài liệu này giúp các thành viên trong nhóm tra cứu nhanh và nắm bắt luồng hệ thống theo đúng nhu cầu.

---

## 🎯 1. Lộ Trình Đọc Tài Liệu Theo Nhu Cầu (Reading Paths)

| Bạn là ai? | Mục tiêu cần làm | Thứ tự đọc khuyến nghị |
| :--- | :--- | :--- |
| **Thành viên mới tham gia dự án** | Hiểu toàn bộ hệ thống, kiến trúc, pipeline và cách code chạy | 1. [`TEAM_TECHNICAL_REPORT.md`](TEAM_TECHNICAL_REPORT.md)<br>2. [`WINDOWS_SETUP.md`](WINDOWS_SETUP.md)<br>3. [`CCTV_UI_WORKFLOW.md`](CCTV_UI_WORKFLOW.md) |
| **Người vận hành / Chạy thử demo** | Khởi động server, chạy UI, test tìm kiếm, traffic, phân bổ | 1. [`CCTV_UI_WORKFLOW.md`](CCTV_UI_WORKFLOW.md)<br>2. [`DEMO_DATA.md`](DEMO_DATA.md)<br>3. [`CCTV_FULLVIDEO_COLAB.md`](CCTV_FULLVIDEO_COLAB.md) |
| **Phát triển thuật toán / Backend** | Mở rộng tính năng phát hiện sự cố, homography, solver MILP | 1. Phân hệ quan tâm bên dưới (Mục 3)<br>2. [`CCTV_PROJECT_REPORT.md`](CCTV_PROJECT_REPORT.md)<br>3. [`CCTV_PROGRESS.md`](CCTV_PROGRESS.md) |
| **Kiểm thử viên / Đánh giá bàn giao** | Chạy smoke test, kiểm tra second-machine handover | 1. [`CCTV_HANDOVER.md`](CCTV_HANDOVER.md)<br>2. [`CCTV_DEMO_SMOKE.md`](CCTV_DEMO_SMOKE.md)<br>3. [`CCTV_BENCHMARK.md`](CCTV_BENCHMARK.md) |

---

## 📑 2. Danh Mục Tài Liệu Cốt Lõi (Core & Executive Documents)

* **[`TEAM_TECHNICAL_REPORT.md`](TEAM_TECHNICAL_REPORT.md)** ⭐ **(Đọc đầu tiên):** Báo cáo kỹ thuật toàn diện viết riêng cho nhóm: tóm tắt bài toán, kiến trúc 12 stage (kèm sơ đồ Mermaid), 7 phân hệ kỹ thuật, 5 tab Web UI, bản đồ code, hướng dẫn vận hành và 5 nguyên tắc kỹ thuật bất biến.
* **[`CCTV_PROJECT_REPORT.md`](CCTV_PROJECT_REPORT.md):** Báo cáo kỹ thuật chi tiết gốc của dự án: ghi chép kết quả thực nghiệm, độ trễ HTTP benchmark, bằng chứng lịch sử và các ranh giới phương pháp luận (methodological limits).
* **[`CCTV_PROGRESS.md`](CCTV_PROGRESS.md):** Nhật ký theo dõi tiến độ phát triển, bảng đối chiếu kế hoạch các Milestone (M0 $\rightarrow$ M11) và các task đã hoàn thành.

---

## 🚀 3. Hướng Dẫn Vận Hành & Môi Trường (Runtime & Operations)

* **[`WINDOWS_SETUP.md`](WINDOWS_SETUP.md):** Hướng dẫn cấu hình môi trường chuẩn Windows 11 Native, cài đặt Python 3.11, CUDA Toolkit 12.6, PyTorch cu126 và các thư viện cần thiết.
* **[`CCTV_UI_WORKFLOW.md`](CCTV_UI_WORKFLOW.md):** Hướng dẫn chi tiết sử dụng giao diện web hợp nhất: tải video, gán camera, căn chỉnh ROI, chạy 12 công đoạn, xem tiến độ và xuất báo cáo kiểm định ZIP.
* **[`CCTV_FULLVIDEO_COLAB.md`](CCTV_FULLVIDEO_COLAB.md):** Hướng dẫn xuất việc (job export) sang Google Colab GPU để bám vết toàn bộ video (full-video tracking 0.5s) và nhập ngược kết quả vào máy Windows.
* **[`DEMO_DATA.md`](DEMO_DATA.md):** Khai báo danh mục 6 video mẫu từ camera N001/N002 trong `Demo_Data/`, nguyên tắc bất biến chỉ đọc (read-only) và cấu hình manifest.
* **[`CCTV_EXECUTION_OPTIONS_PLAN.md`](CCTV_EXECUTION_OPTIONS_PLAN.md):** Kế hoạch chi tiết hỗ trợ chuyển đổi linh hoạt giữa xử lý máy cục bộ và xử lý theo đợt qua Google Colab.

---

## 🧩 4. Đặc Tả Chi Tiết Từng Phân Hệ Kỹ Thuật (Subsystem Specifications)

### 4.1 Ingestion & Video Processing
* **[`CCTV_INGESTION.md`](CCTV_INGESTION.md):** Luồng trích xuất keyframe ~2 FPS, lưu vết Presentation Time Stamp (PTS) chính xác và xử lý video mẫu.
* **[`M0_INVENTORY.md`](M0_INVENTORY.md):** Bảng kiểm kê khảo sát ban đầu về dữ liệu, phần cứng máy và các thành phần kế thừa.

### 4.2 Truy hồi đa phương thức (Multimodal Retrieval)
* **[`CCTV_RETRIEVAL.md`](CCTV_RETRIEVAL.md):** Mô hình CLIP ViT-L/14 FP16, kỹ thuật trích xuất vector, chỉ mục FAISS `IndexFlatIP(768)` và tra cứu SQLite.
* **[`CCTV_DEMO_UI.md`](CCTV_DEMO_UI.md):** FastAPI backend phục vụ tìm kiếm text/image và API tua video (video seek) mượt mà đến đúng giây xuất hiện ảnh.
* **[`CCTV_RUNTIME_BENCHMARK.md`](CCTV_RUNTIME_BENCHMARK.md):** Phương pháp đo đạc và báo cáo độ trễ HTTP request tìm kiếm (P50 < 60ms cho text, < 130ms cho image).

### 4.3 Nhận diện & Bám vết phương tiện (Detection & Tracking)
* **[`CCTV_DETECTION.md`](CCTV_DETECTION.md):** Nhận diện xe với YOLOv8 (batch adaptive 4 $\rightarrow$ 2 $\rightarrow$ 1 chống tràn VRAM) và lưu checkpoint.
* **[`CCTV_TRACKING.md`](CCTV_TRACKING.md):** Bám vết đa đối tượng ByteTrack trên frame gốc theo segment 5 giây, kiểm soát ID và bộ nhớ.
* **[`CCTV_TRACKING_OVERLAY.md`](CCTV_TRACKING_OVERLAY.md):** Thuật toán ghép tracklets hình học (geometric stitching) và sinh video có gắn bounding box/ID phục vụ trực quan hóa.
* **[`CCTV_OVERLAY_UI.md`](CCTV_OVERLAY_UI.md):** Tích hợp luồng phát video overlay vào giao diện Web demo.

### 4.4 Thống kê giao thông & Vùng quan tâm (Traffic & Congestion)
* **[`CCTV_TRAFFIC.md`](CCTV_TRAFFIC.md):** Schema dữ liệu detections/tracks, định nghĩa đa giác ROI theo camera và ma trận chuẩn hóa.
* **[`CCTV_CONGESTION.md`](CCTV_CONGESTION.md):** Tính diện tích chiếm dụng hợp (occupancy union), vận tốc và quy tắc xác định trạng thái ùn tắc giao thông.
* **[`CCTV_TRAFFIC_UI.md`](CCTV_TRAFFIC_UI.md):** Giao diện Tab Giao thông: hiển thị ROI, đồ thị timeline lưu lượng và vận tốc theo từng cửa sổ 2 giây.

### 4.5 Phát hiện nghi sự cố & Thẩm định (Incidents & Review)
* **[`CCTV_INCIDENTS.md`](CCTV_INCIDENTS.md):** Quy tắc thời gian-không gian Move-Then-Stop, bộ lọc bối cảnh hàng xe (Queue-Context Filter) chống cảnh báo nhầm khi dừng đèn đỏ.
* **[`CCTV_EVENTS.md`](CCTV_EVENTS.md):** Quy trình đóng gói bằng chứng nghi sự cố (evidence video clip) và sổ nhật ký thẩm định (review journal) bảo mật bằng hash-chain.
* **[`CCTV_EVENTS_UI.md`](CCTV_EVENTS_UI.md):** Giao diện Tab Sự kiện: duyệt video bằng chứng và cập nhật trạng thái thẩm định bởi kiểm định viên.
* **[`CCTV_REVIEW_NOTES.md`](CCTV_REVIEW_NOTES.md):** Quy định ghi chú thẩm định độc lập (double-blind review) phục vụ tạo nhãn ground truth.
* **[`CCTV_REVIEW_PREPARATION.md`](CCTV_REVIEW_PREPARATION.md):** Chuẩn bị biểu mẫu đánh giá và cách ly dữ liệu giữa người gán nhãn.
* **[`CCTV_ADJUDICATION.md`](CCTV_ADJUDICATION.md):** Quy trình phân giải sai lệch (adjudication) giữa hai kiểm định viên độc lập.

### 4.6 Tối ưu hóa phân bổ lực lượng (Police Dispatch Optimization)
* **[`CCTV_RESOURCES.md`](CCTV_RESOURCES.md):** Khai báo tài nguyên tổ tuần tra mô phỏng (quân số, khu vực phụ trách, kỹ năng, đội hình dự phòng).
* **[`CCTV_DISPATCH.md`](CCTV_DISPATCH.md):** Bộ giải quy hoạch tuyến tính nguyên nhị phân HiGHS MILP 100x100 với hàm mục tiêu từ điển và chứng chỉ số học nguyên.
* **[`CCTV_DISPATCH_UI.md`](CCTV_DISPATCH_UI.md):** Giao diện Tab Phân bổ: hiển thị ma trận điều động, lộ trình di chuyển và lý do chưa thể phân bổ (unmet reasons).

---

## 🧪 5. Đánh Giá Chất Lượng, Kiểm Thử & Bàn Giao (Evaluation & QA)

* **[`CCTV_HANDOVER.md`](CCTV_HANDOVER.md):** Tài liệu hướng dẫn đóng gói bàn giao, kịch bản tự động kiểm tra tương thích trên máy Windows thứ hai (`Test-SecondMachineHandover.ps1`).
* **[`CCTV_DEMO_SMOKE.md`](CCTV_DEMO_SMOKE.md):** Kịch bản kiểm thử khói tự động một lệnh (`scripts/run_cctv_demo_smoke.py`).
* **[`CCTV_DEMO_ACCEPTANCE.md`](CCTV_DEMO_ACCEPTANCE.md):** Tiêu chuẩn nghiệm thu chức năng demo (kết nối API, thời gian phát video, tải ZIP).
* **[`CCTV_BENCHMARK.md`](CCTV_BENCHMARK.md):** Bộ công cụ tính toán Temporal IoU, Precision, Recall, F1 trên nhãn sự kiện.
* **[`CCTV_EVENT_EVALUATION.md`](CCTV_EVENT_EVALUATION.md):** Kế hoạch phân chia tập Train/Val/Test (splits) chống rò rỉ dữ liệu camera (leakage).
* **[`CCTV_M10_EVALUATION.md`](CCTV_M10_EVALUATION.md):** Tổng kết đánh giá năng lực hệ thống tại Milestone M10 trước khi đóng gói demo M11.

---

## 🏛️ 6. Tài Liệu Nền Tảng Kế Thừa (Legacy Foundation Material)

Các tài liệu thuộc dự án tìm kiếm video đa phương thức nền tảng trước khi chuyên biệt hóa sang CCTV:
* **[`KE_THUA_VA_PHAT_TRIEN_DO_AN.md`](KE_THUA_VA_PHAT_TRIEN_DO_AN.md):** Phân tích các thành phần có thể tái sử dụng và đề xuất kế hoạch phát triển 8 tuần.
* **[`ARCHITECTURE.md`](ARCHITECTURE.md):** Cấu trúc kiến trúc dịch vụ FastAPI và vanilla JS của ứng dụng đa phương thức gốc.
* **[`PIPELINE_MAP.md`](PIPELINE_MAP.md):** Sơ đồ các công đoạn OCR, ASR, trích xuất frame của hệ thống đa phương thức ban đầu.
* **[`BASELINE_VALIDATION.md`](BASELINE_VALIDATION.md):** Kết quả kiểm thử bộ mã cơ sở ban đầu.
* **[`CLEANUP_GUIDE.md`](CLEANUP_GUIDE.md):** Quy tắc quản lý dung lượng ổ đĩa và ngăn chặn đẩy dữ liệu lớn lên Git.
* **[`archive/aic-2026/`](archive/aic-2026/):** Thư mục lưu trữ các tài liệu thi đấu AIC 2026 cũ (tham khảo lịch sử).
