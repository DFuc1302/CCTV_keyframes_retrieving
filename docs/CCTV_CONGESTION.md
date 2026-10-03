# T3.4 — Chỉ số giao thông và congestion candidate

## Khái niệm và kiến trúc

`pipeline/cctv/congestion.py` đọc checkpoint T3.3 ở chế độ SQLite read-only,
không nạp model, không sửa video, tracks hoặc index. Đây là baseline CPU để
chạy thử chỉ số và persistence; ngưỡng chưa được học hoặc hiệu chỉnh theo nhãn.

| Chỉ số | Định nghĩa |
| --- | --- |
| Vehicle count | Số bbox có tâm đáy nằm trong hoặc trên biên polygon ROI, tại từng frame; không phải số xe duy nhất đi qua |
| Occupancy | Diện tích hợp các bbox giao ROI chia diện tích ROI trên raster; không cộng đôi phần overlap |
| Motion | Median dịch chuyển tâm đáy bbox theo pixel ảnh gốc / chênh lệch PTS, với cùng track key và hai tâm đều trong ROI |
| Metric speed (tùy chọn) | Median dịch chuyển tâm đáy bbox trên mặt đất / chênh lệch PTS × 3,6, đơn vị km/h; chỉ khi có hiệu chuẩn mặt đường và hai điểm nằm trong vùng hiệu lực |
| Coverage | Tổng thời gian có cặp frame liên tiếp, PTS gap hợp lệ, chia thời lượng cửa sổ |

ROI mask rộng 320 pixel, chiều cao giữ tỷ lệ ảnh (1080p: 180 pixel). Polygon
vẽ bằng [OpenCV fillPoly](https://docs.opencv.org/4.11.0/d6/d6e/group__imgproc__draw.html),
count dùng pointPolygonTest trên tọa độ chuẩn hóa. Occupancy là xấp xỉ raster,
có sai số biên và ảnh hưởng perspective; bbox không phải segmentation xe và
diện tích ảnh không phải diện tích mặt đường tính bằng mét vuông.

Mean count/occupancy được trọng số theo thời gian: frame tại t được giữ trên
`[t, next_t)` chỉ khi gap không quá 0.5 giây. Khoảng gap lớn không được nội suy;
không kéo frame cuối đến hết clip. Motion là median các cặp quan sát, không
trọng số theo thời gian. Không nối ID qua segment, video hoặc run. Motion
thiếu là null, không phải 0; count bằng 0 là kết quả frame đã xử lý, không
thay thế frame bị mất. Chuyển động ảnh không phải tốc độ km/h; metric speed là
chỉ số riêng, không thay thế motion hoặc đổi các quyết định dense/slow.

Window demo 2 giây. Một cửa sổ đủ bằng chứng khi coverage >=0.90, đủ thời lượng
2 giây và có >=10 cặp motion. Dense/slow yêu cầu đồng thời occupancy >=0.25,
mean count >=5, median motion <=12 px/s. Ba cửa sổ liên tiếp (6 giây) mới là
`congestion_candidate`; cửa sổ thiếu bằng chứng/không đáp ứng điều kiện hoặc
khoảng trống reset persistence. Đây là **giá trị chạy thử**, không phải ngưỡng
giao thông phổ quát. Camera cần ROI và nhãn để hiệu chỉnh; đèn đỏ, perspective,
box jitter và ID phân mảnh có thể gây sai lệch.

Memory được giới hạn bằng đọc từng frame, hai raster nhỏ, và giải phóng các
cặp motion của cửa sổ đã đóng sau khi lấy median. Tối đa 100.000 cặp/window,
100.001 chỉ số window/video, raster height <=2.048; vượt giới hạn phải giảm
window_sec hoặc phạm vi chạy. Không giữ ảnh hoặc tất cả track observations
của toàn video trong RAM.

## Hiệu chuẩn homography tùy chọn

`--calibration-config` nhận `CalibrationConfig` schema version 1, tách biệt
với ROI và ngưỡng congestion. File demo `configs/cctv.calibration.demo.json`
chỉ khai báo N001/N002 pending, `calibrations` rỗng: **chưa có số đo thực địa**.
Không tự điền chiều rộng làn hoặc khoảng vạch sơn từ giả định.

Để tạo cấu hình thực, cần 4–64 cặp mốc không suy biến đã đo vật lý trên cùng
mặt đường phẳng. Mỗi cặp có `image_point: [u,v]` chuẩn hóa trong `[0,1]` và
`ground_point_meters: [X,Y]` hữu hạn trong một hệ trục chung, đơn vị mét.
X/Y không giới hạn `[0,1]`, có thể âm. Mỗi camera khai báo `calibration_version: 1`
và `roi_polygon` chuẩn hóa làm vùng hiệu lực; không suy đoán chất lượng ngoài
vùng đó. Camera đã có calibration không đồng thời nằm trong `pending_cameras`;
camera lặp hoặc không thuộc manifest bị từ chối. Metadata `reviewer`/
`reviewed_at` là tùy chọn đi cùng nhau, timestamp cần timezone; không tự tạo
review/approval. Chi tiết hợp đồng: [CCTV_TRAFFIC.md](CCTV_TRAFFIC.md).

Phép chiếu H dùng tâm đáy bbox **chuẩn hóa**, không dùng tọa độ pixel làm đầu
vào H. Với `[X',Y',W'] = H × [u,v,1]`, mặt đất là `[X'/W',Y'/W']`.
Metric speed của cặp hợp lệ là `3.6 × hypot(ΔX,ΔY) / ΔPTS`. Cả hai quan sát
phải thuộc ROI giao thông và vùng hiệu lực hiệu chuẩn, cùng track key và
PTS gap hợp lệ. Phép chiếu không khả dụng hoặc điểm ngoài vùng hiệu lực
không tạo cặp tốc độ; không thay chúng bằng vận tốc 0. Signal ghi riêng
`metric_motion_pairs` (mặc định 0) với `motion_pairs` pixel để không nhầm
độ phủ của hai phép đo.

Window có `median_speed_kmh` (không âm hoặc null) và `calibration_sha256`
(SHA256 calibration của camera hoặc null). Window lịch sử thiếu trường mới vẫn được
nạp với mặc định null. Không truyền calibration hoặc camera còn pending:
metric speed không khả dụng, pixel motion và điều kiện congestion giữ nguyên.
Có geometry cũng không làm `thresholds_calibrated`/`operational_ready` thành
true và không xác nhận M8 hoặc duyệt ROI.

Homography chỉ đúng cho mặt phẳng đã đo: dốc biến đổi, cầu, mặt đường không
phẳng hoặc chuyển động ngoài mặt đường có thể sai. Camera rung/đổi góc/zoom,
thay độ phân giải với crop khác, bbox jitter, occlusion, ID resets và sai
PTS là giới hạn quan trọng. Đo lại khi hình học camera thay đổi và đối chiếu
tốc độ với nguồn đo độc lập trước khi dùng nghiệp vụ. Kiểm thử tổng hợp
chứng minh phép tính, không chứng minh tốc độ N001/N002 thực tế.

## ROI nháp và output

Theo yêu cầu chạy thử, `configs/cctv.roi-draft.json` chứa polygon nháp cho
N001/N002 với **status=pending**, không gán người duyệt. N001 đo một phần
giao lộ/đường chính, N002 tập trung phần đường phía phải dải phân cách; đây
không phải toàn bộ làn hoặc diện tích đường đã xác minh. Cấu hình pending
không có polygon ban đầu `configs/cctv.traffic.demo.json` vẫn giữ nguyên.

Chỉ khi bật `--allow-draft` pipeline mới chấp nhận ROI nháp. Output dùng
`kind=draft_window`, `roi_status=pending`, `demo_only=true`, roi_ready=false.
Không đổi schema T3.1/T3.3 hoặc fake approval. Chế độ thường chỉ nhận ROI có
reviewed_by/reviewed_at và status approved, xuất TrafficWindow version 1;
operational_ready vẫn false vì chưa hiệu chỉnh ngưỡng. Đổi ROI/ngưỡng/chế độ
hoặc code cần output-dir mới.

Output riêng tại `outputs/demo-data/traffic-draft-v1`:

| File | Nội dung |
| --- | --- |
| `windows.jsonl` | Các TrafficWindow hoặc DraftWindow |
| `signals.jsonl` | Window cùng coverage, motion_pairs pixel, metric_motion_pairs, status, persistence và cờ demo/calibration |
| `windows.db` | Checkpoint toàn video, checksum payload |
| `window_manifest.json` | Recipe/input hashes, tổng windows/candidates và readiness |
| `window_state.json`, `windows.log` | Trạng thái resume/publication và lỗi frame/video |

Video chỉ commit khi mọi frame hợp lệ. Lỗi schema/PTS/locator được log, hủy
kết quả video đó, tiếp tục video khác; không publish nếu còn lỗi. Ctrl+C
trong video khiến video đó chạy lại; video đã commit được skip. Publication
có journal và checksum để phục hồi gián đoạn. Output lạ/bị sửa được giữ và
báo lỗi. Resume complete xác minh hashes rồi skip, không rewrite artifacts.

Nguồn metric được kiểm tra bằng `window.calibration_sha256` đối chiếu map
`camera_calibration_sha256` trong manifest. Hash file cấu hình nằm riêng ở
`signature.calibration_sha256`; signature ràng buộc cấu hình/input/code.
Đổi file calibration (kể cả từ pending sang đo thật) cần tái tính ở
output-dir mới, không tái dùng checkpoint cũ hoặc sửa output đã niêm phong.

## Code và lệnh PowerShell

Code chạy đầy đủ: `pipeline/cctv/congestion.py`, `scripts/preview_cctv_roi.py`.
Không thêm dependency; dùng OpenCV/Numpy/Pillow/Pydantic/SQLite đã có.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Test-Windows.ps1

.\.venv\Scripts\python.exe scripts\run_python_logged.py `
  --log outputs\demo-data-review\congestion-v1-console.txt -- `
  -m pipeline.cctv.congestion `
  --manifest configs\cctv.demo.json `
  --roi-config configs\cctv.roi-draft.json `
  --metrics-config configs\cctv.congestion.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output-dir outputs\demo-data\traffic-draft-v1 --allow-draft

Get-Content outputs\demo-data\traffic-draft-v1\window_manifest.json
Get-Content outputs\demo-data\traffic-draft-v1\signals.jsonl -TotalCount 2

.\.venv\Scripts\python.exe scripts\preview_cctv_roi.py `
  --manifest configs\cctv.demo.json `
  --roi-config configs\cctv.roi-draft.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output-dir outputs\demo-data\roi-draft-previews
```

Chạy lại cùng lệnh để kiểm tra resume. Bỏ `--allow-draft` với config nháp sẽ
trả exit 2 trước khi tạo output. CLI exit 1 khi có video lỗi, 130 khi Ctrl+C.
ROI previews ghi PNG mới và giữ input JPEG; skip khi checksum khớp.

### Chạy với calibration pending hoặc số đo đã thu thập

Lệnh sau giữ nguyên tracking/ROI/raw đã có và ghi sang output mới. File demo
pending không tạo km/h; chỉ thay đường dẫn calibration bằng file đã đo thực
khi có số đo và vùng hiệu lực tương ứng, không sửa demo pending để giả lập
số đo của N001/N002:

```powershell
.\.venv\Scripts\python.exe -m pipeline.cctv.congestion `
  --manifest configs\cctv.demo.json `
  --roi-config configs\cctv.roi-draft.json `
  --metrics-config configs\cctv.congestion.demo.json `
  --calibration-config configs\cctv.calibration.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output-dir outputs\demo-data\traffic-draft-metric-pending-v1 --allow-draft
```

Bỏ `--calibration-config` giữ chế độ pixel-only. Cả hai chế độ giữ nguyên
ngưỡng px/s; không dùng km/h để suy rằng ngưỡng demo đã được hiệu chỉnh.

### Xem trong UI

Sau khi pipeline publish thành công, khởi động lại server để nạp snapshot
mới; chỉnh artifacts khi server đang chạy sẽ làm traffic unavailable.
Với output pending của lệnh trên:

```powershell
$env:CCTV_TRAFFIC_DIR = (Resolve-Path outputs\demo-data\traffic-draft-metric-pending-v1).Path
.\scripts\Start-CctvDemo.ps1 -Port 8080
```

Mở `http://127.0.0.1:8080/`, chọn tab **Giao thông** (`#traffic-section-tab`),
chọn camera/video rồi chọn cột timeline hoặc thời gian trong bảng.
API `/cctv/traffic` báo availability; `/cctv/traffic/windows/{video_id}`
trả `rows[].window.median_speed_kmh` và `rows[].window.calibration_sha256`.
`#metric-motion` vẫn hiển thị px/s; `#metric-speed` và cột **Tốc độ (km/h)**
hiển thị riêng km/h. Trường null hoặc thiếu trong output lịch sử hiển thị
**Chưa khả dụng**, không hiển thị 0 hoặc NaN. `#traffic-calibration` hiển thị
SHA256 calibration của camera khi có, không diễn giải hash thành approval.
`#traffic-evidence` tách số cặp pixel và metric. Geometry không chứng minh
chất lượng tốc độ thực hoặc ngưỡng congestion; cảnh báo demo vẫn giữ nguyên.

## Nghiệm thu chạy thử 2026-10-01

92 core tests đạt trên Windows PowerShell 5.1, runtime FP16/NMS đạt; thêm 18
tests T3.4 gồm union/count, PTS-weighted means, pixel motion, reset scope,
persistence trên xe đứng yên tổng hợp, gaps, ROI gate, corruption, frame
failure logging, checkpoint/publication recovery và preview resume.

Demo thật: 6 clip `[30,40)`, 1.501 frame, 30 cửa sổ, không lỗi. Coverage thấp
nhất 98.15%; tất cả signals là `no_candidate` với ngưỡng demo đã ghi. Không
suy rằng video không có tắc đường từ kết quả này. Resume skip sáu video,
hash/mtime sáu output artifacts/state/log giữ nguyên. Preview tạo sáu PNG,
chạy lại skip đủ. Raw sáu SHA256/mtime, 18 tracking artifacts/state/log,
11 detector artifacts/state/log và bốn retrieval artifacts giữ nguyên.

Bằng chứng cục bộ: `outputs/demo-data-review/congestion-verification.json`,
`tracking-verification.json`, `detection-verification.json`, test log
`outputs/tests/core-tests.txt`. Dữ liệu/ảnh/DB trong outputs không push.

Task tiếp theo **T3.5**: đưa ROI overlay, thống kê theo camera/video và timeline
vào demo API/UI, giữ rõ cờ draft/uncalibrated. Review ROI và tạo nhãn để hiệu
chỉnh ngưỡng vẫn mở; chưa nhận diện tai nạn hoặc phân bổ cảnh sát thực tế.

## Git

```powershell
git add pipeline/cctv/congestion.py scripts/preview_cctv_roi.py `
  configs/cctv.roi-draft.json configs/cctv.congestion.demo.json `
  test_cctv_congestion.py scripts/Test-Windows.ps1 `
  docs/CCTV_CONGESTION.md docs/CCTV_PROGRESS.md README.md README_CCTV.md
git commit -m 'feat: add draft ROI traffic windows and congestion persistence baseline'
git push origin main
```
