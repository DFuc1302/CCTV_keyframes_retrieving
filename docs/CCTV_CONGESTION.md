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
thay thế frame bị mất. Chuyển động ảnh không phải tốc độ km/h.

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
| `signals.jsonl` | Window cùng coverage, motion_pairs, status, persistence và cờ demo/calibration |
| `windows.db` | Checkpoint toàn video, checksum payload |
| `window_manifest.json` | Recipe/input hashes, tổng windows/candidates và readiness |
| `window_state.json`, `windows.log` | Trạng thái resume/publication và lỗi frame/video |

Video chỉ commit khi mọi frame hợp lệ. Lỗi schema/PTS/locator được log, hủy
kết quả video đó, tiếp tục video khác; không publish nếu còn lỗi. Ctrl+C
trong video khiến video đó chạy lại; video đã commit được skip. Publication
có journal và checksum để phục hồi gián đoạn. Output lạ/bị sửa được giữ và
báo lỗi. Resume complete xác minh hashes rồi skip, không rewrite artifacts.

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
