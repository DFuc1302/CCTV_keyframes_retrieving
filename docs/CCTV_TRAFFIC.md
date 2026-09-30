# T3.1 — Hợp đồng dữ liệu giao thông và ROI

## Concept và kiến trúc

`pipeline/cctv/traffic.py` định nghĩa schema version 1 bằng Pydantic đã có trong
runtime FastAPI. Không thêm dependency, nạp model, sửa ingestion manifest hoặc
ghi vào database/index retrieval. CLI chỉ đọc cấu hình; trạng thái ROI độc lập
với `/ready` của ứng dụng tìm kiếm.

`configs/cctv.traffic.demo.json` phải phủ đúng một lần mỗi camera canonical trong
manifest ingestion. N001 và N002 ban đầu có `status=pending`, `polygon=null`.
Đây là cấu hình chưa duyệt, không phải vùng đường toàn ảnh được tự chấp nhận.
Draft polygon có thể lưu ở trạng thái pending; approved yêu cầu polygon hợp lệ,
reviewed_by không rỗng và reviewed_at ISO 8601 có timezone. Review time là thời
điểm duyệt ROI, không phải thời gian ghi hình CCTV.

Polygon dùng `[x,y]` chuẩn hóa trong `[0,1]`, gồm 3–64 đỉnh theo biên, không lặp
đỉnh đầu ở cuối. Cho phép cả hai chiều và polygon lõm; từ chối điểm trùng, diện
tích bằng không, cạnh chồng lấn hoặc tự cắt. Sai số `1e-12` cho kiểm tra thẳng
hàng và `1e-9` cho twice-area là dung sai số học trên tọa độ chuẩn hóa, không
phải ngưỡng congestion. Khi camera đổi góc, cần duyệt cấu hình mới.

Contract frozen, reject unknown fields, non-finite numbers, bool thay số và
phiên bản schema không hỗ trợ. File JSON dùng arrays; Python contract dùng tuples.
Validation tham chiếu qua `validate_record(json_text, manifest, config)`:

| Record | Nội dung và ràng buộc |
| --- | --- |
| `DetectionFrame`, kind `detections` | run_id, locator, width/height dương, detections; list rỗng là frame đã xử lý không có detection |
| `Detection` | label bicycle/motorcycle/car/bus/truck, confidence `[0,1]`, bbox normalized xyxy có diện tích dương |
| `TrackObservation`, kind `track` | detection và locator, run_id/segment_id/track_id; track_key gồm run/camera/video/segment/id |
| `TrafficWindow`, kind `window` | khoảng `[start_sec,end_sec)`, sample_count dương, mean_vehicle_count, mean_occupancy, median_motion_px_per_sec hoặc null; camera phải có ROI approved |

Locator giữ `camera_id`, `video_id`, `timestamp_sec` tương đối từ frame đầu được
giải mã, `frame_idx` ordinal 0-based. Camera/video phải khớp manifest. Chưa tự
kiểm tra frame_idx/PTS với map hoặc duration: bước pipeline T3.2/T3.3 phải lấy
locator từ frame giải mã thật. Frame_idx không phải số thứ tự file keyframe.
Không suy timestamp tuyệt đối, geolocation hoặc km/h từ dữ liệu hiện tại.

Window occupancy dự kiến là phần diện tích ROI bị các box xe che phủ, union để
không đếm đôi phần giao nhau; count dự kiến đếm tâm đáy box trong ROI. Đây là
định nghĩa cho stage thống kê tiếp theo, T3.1 chưa tính các giá trị này. Motion
phải xác định từ track và PTS thật, chưa có chuyển đổi pixel sang mét.

Detection/tracking baseline được phép chạy trước khi duyệt ROI. Thống kê ROI
không được gán approved chỉ bằng chuỗi trong record: validator đối chiếu cấu
hình camera thực tế. Không có record tai nạn, score congestion hoặc đề xuất
dispatch trong schema này.

## Chạy và kiểm thử PowerShell

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m pipeline.cctv.traffic --manifest configs\cctv.demo.json --config configs\cctv.traffic.demo.json

# Gate cho stage yêu cầu ROI: hiện trả exit 1 vì chưa duyệt N001/N002
.\.venv\Scripts\python.exe -m pipeline.cctv.traffic --manifest configs\cctv.demo.json --config configs\cctv.traffic.demo.json --require-approved
$LASTEXITCODE

.\.venv\Scripts\python.exe -m unittest -v test_cctv_traffic test_cctv_manifest
# Dừng server model trước full suite nếu máy đang thiếu RAM/VRAM
.\scripts\Test-Windows.ps1
```

CLI: exit 0 khi cấu hình hợp lệ; exit 1 khi yêu cầu approved chưa đạt; exit 2
khi lỗi schema/path. Không ghi output, đổi raw hay tải model. Các record sau này
sẽ được pipeline lưu JSONL trong output riêng, kèm provenance model/recipe và
checkpoint/resume ở T3.2/T3.3; chưa có pipeline tạo detections trong task này.

Ví dụ record hoàn chỉnh cho unit test (dữ liệu tổng hợp, không phải detection thật):

```json
{
  "schema_version": 1,
  "kind": "detections",
  "run_id": "run-001",
  "locator": {
    "camera_id": "N001",
    "video_id": "N001-V001",
    "timestamp_sec": 249.519,
    "frame_idx": 6238
  },
  "width": 1920,
  "height": 1080,
  "detections": [
    {"label": "motorcycle", "confidence": 0.8, "bbox": [0.1, 0.2, 0.4, 0.7]}
  ]
}
```

Tám tests mới kiểm tra approval/timezone, topology/bounds, canonical camera
coverage, locator, scalar/box lỗi, track resets và window ROI gate. Không phải
đánh giá chất lượng detector. Việc duyệt vùng đường N001/N002 vẫn còn mở.
Nghiệm thu 2026-09-30: **55 core tests đạt** trên Windows PowerShell 5.1,
cùng runtime CUDA FP16/NMS; CLI demo schema hợp lệ và ROI chưa ready đúng thiết kế.

## Git

```powershell
git diff --check
git add -- pipeline/cctv/traffic.py configs/cctv.traffic.demo.json test_cctv_traffic.py scripts/Test-Windows.ps1 docs/CCTV_TRAFFIC.md docs/CCTV_PROGRESS.md README_CCTV.md
git diff --cached --check
git commit -m "feat: add validated CCTV traffic contracts and ROI configuration"
git push origin main
```
