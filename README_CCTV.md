# CCTV keyframe retrieval — VGU Explore Computer Science

Project kế thừa hệ thống tìm kiếm video đa phương thức, mở rộng sang truy hồi CCTV, phát hiện nghi tai nạn/tắc đường và hỗ trợ phân bổ cảnh sát. Môi trường mục tiêu: Windows 11 Native, PowerShell, RTX 4070 Laptop 8GB.

Đọc `docs/M0_INVENTORY.md` để biết nguồn kế thừa, môi trường đã khảo sát và các điều kiện còn thiếu. `README.md` là tài liệu legacy, chứa hướng dẫn WSL và không đại diện cho quy trình Windows mới.

Hướng dẫn runtime Windows: `docs/WINDOWS_SETUP.md`.

Tracking toàn video mỗi 0,5 giây, tối ưu tốc độ và Colab batch qua UI:
[hướng dẫn](docs/CCTV_FULLVIDEO_COLAB.md),
[notebook Colab](https://colab.research.google.com/github/DFuc1302/CCTV_keyframes_retrieving/blob/main/notebooks/CCTV_Colab.ipynb).

Tiến độ đối chiếu kế hoạch và task tiếp theo: `docs/CCTV_PROGRESS.md`.

Schema detections/tracks/traffic windows và ROI theo camera: `docs/CCTV_TRAFFIC.md`.

Baseline phát hiện xe YOLO trên keyframes, checkpoint/resume và overlays:
`docs/CCTV_DETECTION.md`.

Tracking video gốc, PTS và segment resume: `docs/CCTV_TRACKING.md`.
Chạy thử ROI nháp, traffic windows và congestion persistence bằng CPU:
[`docs/CCTV_CONGESTION.md`](docs/CCTV_CONGESTION.md). ROI và ngưỡng demo chưa
được nghiệm thu; dùng `--allow-draft` để chạy thử mà giữ trạng thái pending.

Tab Giao thông trên demo: [`docs/CCTV_TRAFFIC_UI.md`](docs/CCTV_TRAFFIC_UI.md).
Chọn camera/video, xem ROI và timeline rồi mở evidence đúng timestamp.

Event evidence và review bằng CLI: [`docs/CCTV_EVENTS.md`](docs/CCTV_EVENTS.md).
Package dữ liệu hiện có có 0 ứng viên; bài tập review tổng hợp lưu riêng,
không xác nhận tai nạn/tắc đường trong video. Tab Sự kiện đã đọc được các
package và trạng thái review, mở evidence; UI chưa ghi review.

Baseline nghi sự cố theo thời gian: [`docs/CCTV_INCIDENTS.md`](docs/CCTV_INCIDENTS.md).
Move-then-stop rule chạy CPU từ tracks; có thể bắt nhầm xe chờ đèn đỏ và chưa
được hiệu chỉnh. Kế hoạch nhãn/split/metrics: `docs/CCTV_EVENT_EVALUATION.md`.

Cấu hình khu vực/đội tuần tra mô phỏng: [`docs/CCTV_RESOURCES.md`](docs/CCTV_RESOURCES.md).
Validate budget nhân sự, reserve, kỹ năng, availability và chi phí có hướng;
solver và tab Phân bổ đã có gợi ý mô phỏng, chưa xác nhận vị trí camera thực tế.

Bộ gợi ý phân bổ mô phỏng bằng CLI: [`docs/CCTV_DISPATCH.md`](docs/CCTV_DISPATCH.md).
Kiểm tra review, kỹ năng, travel, cap/reserve và mỗi đội một lần; có unmet
reasons, giữ nhãn mô phỏng và không phát lệnh điều động thực tế.

Annotation/split validator và event evaluator: [`docs/CCTV_BENCHMARK.md`](docs/CCTV_BENCHMARK.md).
Template CCTV giữ pending/unassigned và metrics=null; fixtures phép tính lưu
riêng, chưa có benchmark chất lượng trên nhãn độc lập.

Benchmark retrieval HTTP với input/config đóng băng, P50/P95, checkpoint và
provenance: [`docs/CCTV_RUNTIME_BENCHMARK.md`](docs/CCTV_RUNTIME_BENCHMARK.md).
Client không tải model thứ hai; latency kỹ thuật không thay thế nhãn relevance.

Báo cáo kỹ thuật và ma trận engineering/methodological limits hiện hành:
[`docs/CCTV_PROJECT_REPORT.md`](docs/CCTV_PROJECT_REPORT.md). Queue-context,
optional geometric stitching, surveyed-calibration km/h, expansion 19 cụm
Việt–Anh và numerical-certified MILP 100×100 đã có; không tự tạo quality labels,
ROI approval, recording splits, marker đo thực hoặc physical second-host evidence.

Gói evidence và one-command Windows handover:
[`docs/CCTV_HANDOVER.md`](docs/CCTV_HANDOVER.md).
`.\scripts\Test-SecondMachineHandover.ps1 -ReferenceInventory '<origin-inventory.json>' -OriginMachine '<origin-hostname>'`
kiểm environment/code/source SHA và CUDA/core trong output mới, không autoapprove
full-process/UI, M8 hoặc accuracy. Local tooling rehearsal chờ evidence;
máy thứ hai vật lý chưa chạy. Dữ liệu/review journals không đi kèm Git clone.
Sau dispatch-v2 cutover, regenerate ba snapshots vào folder mới theo handover;
giữ sealed dispatch-v1/publications làm lịch sử, không rewrite.

Tab Sự kiện & evidence: [`docs/CCTV_EVENTS_UI.md`](docs/CCTV_EVENTS_UI.md).
Đọc sealed events/review state và mở video evidence; chưa ghi review qua UI.

Pipeline CCTV mới: `docs/CCTV_INGESTION.md`, dùng manifest độc lập và giữ map/keyframe legacy. Có demo tổng hợp khi chưa có dữ liệu thật.

Dataset demo đã đăng ký: `D:\Project ExploreCS\Demo_Data`, gồm 6 video từ hai camera N001/N002. Manifest: `configs/cctv.demo.json`. Xem `docs/DEMO_DATA.md` để chạy extraction và biết giới hạn đánh giá.

CLIP/FAISS/SQLite và CLI tìm kiếm text/image: `docs/CCTV_RETRIEVAL.md`. Pipeline giữ ID/schema legacy, dùng FP16 trên CUDA và checkpoint theo batch.

Demo FastAPI tìm kiếm text/image và mở video tại timestamp: `docs/CCTV_DEMO_UI.md`.
Sau khi chuẩn bị MP4 previews, chạy `.\scripts\Start-CctvDemo.ps1` và mở
`http://127.0.0.1:8080`. CLIP phục vụ thuộc một process riêng; pipeline chờ
process này thoát trước stage GPU, không sửa video gốc. Windows giới hạn mặc
định native BLAS/OpenMP hai threads, giữ explicit environment overrides.
Cold local UI smoke mới đạt 12/12 không recovery; xem `docs/CCTV_UI_WORKFLOW.md`.

Kiểm thử bundle demo hiện có bằng một lệnh (tự start/check/stop server):

```powershell
.\scripts\Test-CctvDemo.ps1 -RequireOverlays
```

Nếu server đang chạy, dùng `-ExistingServer -Port 8080 -RequireOverlays`.
Xem [`CCTV_DEMO_SMOKE.md`](docs/CCTV_DEMO_SMOKE.md) về phạm vi bundle và
[`CCTV_OVERLAY_UI.md`](docs/CCTV_OVERLAY_UI.md) về video bbox/ID. Smoke kiểm
tra assets hiện tại; không cần journal review hoặc report runtime lịch sử.
Chưa review giữ pending; phải dựng assets theo hướng dẫn handover trước.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Setup-Windows.ps1 -Vision
.\scripts\Test-Preflight.ps1
.\scripts\Test-Windows.ps1
```

Raw CCTV và dữ liệu kế thừa luôn chỉ đọc. Dữ liệu, model, index và báo cáo máy cục bộ không đưa vào Git. Chỉ mục mới và log nằm trong output riêng; kiểm kê không tải model hoặc tự động xây index.
