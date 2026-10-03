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
| `TrafficWindow`, kind `window` | khoảng `[start_sec,end_sec)`, sample_count dương, mean_vehicle_count, mean_occupancy, median_motion_px_per_sec hoặc null; median_speed_kmh và calibration_sha256 tùy chọn; camera phải có ROI approved |

Locator giữ `camera_id`, `video_id`, `timestamp_sec` tương đối từ frame đầu được
giải mã, `frame_idx` ordinal 0-based. Camera/video phải khớp manifest. Chưa tự
kiểm tra frame_idx/PTS với map hoặc duration: bước pipeline T3.2/T3.3 phải lấy
locator từ frame giải mã thật. Frame_idx không phải số thứ tự file keyframe.
Không suy timestamp tuyệt đối hoặc geolocation. km/h chỉ có khi cung cấp hiệu
chuẩn mặt đường riêng; cấu hình demo hiện chưa có số đo thực địa.

Window occupancy dự kiến là phần diện tích ROI bị các box xe che phủ, union để
không đếm đôi phần giao nhau; count dự kiến đếm tâm đáy box trong ROI. Đây là
định nghĩa cho stage thống kê tiếp theo; T3.1 ban đầu chưa tính các giá trị này.
Motion phải xác định từ track và PTS thật; metric speed dùng hợp đồng hiệu chuẩn
mặt đường bên dưới, không diễn giải lại pixel motion thành km/h.

Detection/tracking baseline được phép chạy trước khi duyệt ROI. Thống kê ROI
không được gán approved chỉ bằng chuỗi trong record: validator đối chiếu cấu
hình camera thực tế. Không có record tai nạn, score congestion hoặc đề xuất
dispatch trong schema này.

## Hiệu chuẩn mặt đường và tốc độ metric

`configs/cctv.calibration.demo.json` khai báo `schema_version: 1`,
`calibrations: []`, `pending_cameras: ["N001", "N002"]`. Chưa có số đo mốc đường
cho hai camera này: không dùng chiều rộng làn, khoảng vạch sơn hoặc tọa độ giả
làm số đo N001/N002. Cấu hình pending hợp lệ không tạo tốc độ km/h.

| Contract | Nội dung |
| --- | --- |
| `GroundPointPair` | `image_point: [u,v]` chuẩn hóa `[0,1]`; `ground_point_meters: [X,Y]` hữu hạn, đơn vị mét, không giới hạn `[0,1]` và có thể âm |
| `CameraCalibration` | `camera_id`, `calibration_version: 1`, `points` gồm 4–64 cặp không suy biến, `roi_polygon` là vùng hiệu lực chuẩn hóa, `reviewer`/`reviewed_at` tùy chọn |
| `CalibrationConfig` | `schema_version: 1`, `calibrations`, `pending_cameras` (mặc định rỗng); không lặp camera hoặc khai báo camera ngoài manifest |

Mỗi calibration cần **ít nhất bốn mốc đồng phẳng đã đo vật lý**, phân bố trên
vùng đường cần đo, không thẳng hàng/suy biến trong ảnh hoặc mặt đất. Xác định
hệ tọa độ mặt đất cố định với hai trục X/Y và cùng đơn vị mét cho tất cả mốc;
tọa độ âm hoặc lớn hơn 1 hoàn toàn hợp lệ. Chuẩn hóa vị trí mốc ảnh bằng kích
thước ảnh tương ứng, không chuẩn hóa tọa độ mét. `roi_polygon` tuân thủ cùng
quy tắc polygon chuẩn hóa ở trên, là vùng hiệu lực của phép chiếu, không phải
phê duyệt ROI giao thông. `reviewer` và `reviewed_at` phải cùng có hoặc cùng
không có; khi có, thời gian ISO 8601 cần timezone. Không tự gán người duyệt.

`TrafficWindow.median_speed_kmh` là số hữu hạn không âm hoặc null;
`calibration_sha256` là SHA256 truy vết calibration của camera hoặc null. Record cũ
không có hai trường này vẫn nạp được, mặc định null. Không có hiệu chuẩn hoặc
không có cặp chiếu hợp lệ thì tốc độ là **không khả dụng**, không phải 0.
Điểm tâm đáy bbox ngoài vùng hiệu lực không được ngoại suy để tạo tốc độ.
`median_motion_px_per_sec` vẫn độc lập và giữ nguyên ý nghĩa/đơn vị.

Homography giả định mặt đường cục bộ phẳng. Dốc thay đổi, cầu, mặt đường cong
hoặc xe trên mặt phẳng khác có thể làm sai tốc độ. Camera rung/đổi góc/zoom
hoặc thay crop đòi hỏi đo và hiệu chuẩn lại; bbox jitter, occlusion, ID đổi
và lỗi PTS cũng có thể làm sai chuyển động. Geometry hợp lệ không phải bằng
chứng tốc độ thực đã được kiểm chứng, không duyệt ngưỡng congestion, ROI
hoặc nhãn M8. Cần đối chiếu đo tốc độ độc lập và review người thật.

Để chạy với file hiệu chuẩn, dùng `--calibration-config` của
`pipeline.cctv.congestion`; xem lệnh và cách truy vết output trong
[CCTV_CONGESTION.md](CCTV_CONGESTION.md). Không cần sửa raw, manifest hoặc
cấu hình tracking đã niêm phong.

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
