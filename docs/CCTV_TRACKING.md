# T3.3 — Tracking từ video gốc trên Windows

## Khái niệm và kiến trúc

Module `pipeline/cctv/track.py` mở video gốc **chỉ đọc**, giải mã các frame liên
tiếp bằng PyAV, chạy một YOLOv8m FP16 trên CUDA và liên kết bằng ByteTrack.
Không dùng chuỗi keyframes 2 FPS để thay thế video liên tiếp.
[ByteTrack](https://github.com/FoundationVision/ByteTrack) kết hợp detections
có confidence cao và thấp để phục hồi đối tượng bị che khuất. Baseline cố định
trong `configs/cctv.bytetrack.yaml`: high/new 0.25, low 0.10, match 0.80,
buffer 30 lần update, fuse_score=true. Đây là cấu hình ban đầu theo
[Ultralytics tracking](https://docs.ultralytics.com/modes/track/), chưa hiệu
chỉnh theo camera. Detector tracking nhận confidence 0.10 để giữ candidate
cho bước association confidence thấp; không sửa baseline detector T3.2.
Cấu hình tracker khác baseline bị từ chối để tránh chạy ngoài phạm vi đã kiểm thử.

Timestamp được tính từ `(PTS - PTS đầu video) * time_base`, không từ n/FPS;
frame_idx là thứ tự giải mã toàn video bắt đầu từ 0. Giải mã từ đầu rồi bỏ
frame trước start giúp giữ locator thống nhất với extraction legacy.
[PyAV Frame](https://pyav.org/docs/stable/api/frame.html) cung cấp PTS và
time_base; kiểm thử video tổng hợp có frame bị bỏ và PTS không đều xác nhận
pipeline vẫn giữ thời gian thật. Không suy thời gian tuyệt đối từ chữ trên ảnh.

Detection batch tối đa 4 ảnh; ByteTrack association vẫn cập nhật từng frame
theo thứ tự. Giữ tối đa bốn ảnh inference và một ảnh preview. Segment giữ
tối đa 2.000 payload frame; cần
giảm segment-seconds nếu nguồn FPS cao vượt giới hạn. Không lưu video overlay
đầy đủ vào RAM. Model được giải phóng và CUDA cache được dọn ở cuối stage.
Không chạy stage này đồng thời với một CLIP server trên GPU 8GB.

## Checkpoint, lỗi và định danh

SQLite commit toàn segment trong một transaction. Ctrl+C giữa segment hoặc
lỗi một frame làm segment chưa hoàn chỉnh phải chạy lại; các segment đã commit
được skip. Tracker reset ở **mọi ranh giới segment và video**, kể cả chạy mới,
nên replay không cần khôi phục Kalman state. ID chỉ duy nhất trong tuple
`(run_id, camera_id, video_id, segment_id, track_id)`; không nối cùng một xe
qua segment. Buffer 30 là số frame update, không phải 30 giây.

Lỗi frame được log và hủy segment đó; lỗi decoder dừng video vì không thể
cam kết thứ tự frame sau lỗi. Video khác vẫn được xử lý. PTS thiếu/không tăng,
gap trên 0.5 giây hoặc schema sai bị từ chối. Khoảng gap này là guard kỹ thuật,
không phải ngưỡng phát hiện tai nạn. Chỉ publish khi toàn bộ phạm vi yêu cầu đạt.
Input/model/config/code hashes ràng buộc resume; khi thay đổi recipe cần output
mới. Artifact bị sửa hoặc output không thuộc pipeline được giữ và báo lỗi.

Output riêng:

| File | Nội dung |
| --- | --- |
| `tracks.db` | Checkpoint frame/segment và preview BLOB |
| `frames.jsonl` | Ledger từng frame, gồm cả frame không có track |
| `tracks.jsonl` | TrackObservation tương thích schema T3.1 |
| `previews/*.jpg` | Frame cuối mỗi segment với bbox, ID, 30 điểm quỹ đạo gần nhất |
| `tracking_manifest.json` | Hash recipe, phạm vi, số frame/observation, VRAM và readiness |
| `track_state.json`, `track.log` | Trạng thái resume và lỗi |

`tracking_ready=true` chỉ xác nhận artifacts đã publish. `roi_ready=false`
và `congestion_ready=false` khi ROI chưa duyệt; không biến số observation
hoặc ID phân mảnh thành số xe duy nhất hay lưu lượng thực tế.

## Chạy và kiểm thử bằng PowerShell

Từ repo có weights local đã tải trong T3.2:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: 'av==16.1.0' 'lap==0.5.12'
.\.venv\Scripts\python.exe -m pip check
.\scripts\Test-Windows.ps1

.\.venv\Scripts\python.exe scripts\run_python_logged.py `
  --log outputs\demo-data-review\tracking-console.txt -- `
  -u -m pipeline.cctv.track `
  --manifest configs\cctv.demo.json `
  --traffic-config configs\cctv.traffic.demo.json `
  --model-config outputs\model-cache\yolo\yolo.json `
  --tracker-config configs\cctv.bytetrack.yaml `
  --output-dir outputs\demo-data\tracking-clips `
  --start-sec 30 --duration-sec 10 --segment-seconds 5 --device cuda

Get-Content outputs\demo-data\tracking-clips\tracking_manifest.json
```

Chạy lại lệnh tracking để resume. Có thể lọc `--video-id N001-V001` hoặc chạy
`--full-video` thay cho `--duration-sec`; mỗi phạm vi mới dùng output-dir mới.
Tracking toàn bộ sáu MOV đã đo ở native và 2 FPS ngày 2026-10-02; xem
[so sánh runtime](CCTV_FULLVIDEO_COLAB.md#so-sánh-native-mọi-frame-với-2-fps--2026-10-02).
Chưa có benchmark chất lượng tracking. Chạy bộ unit tests riêng:

```powershell
.\.venv\Scripts\python.exe scripts\run_python_logged.py `
  --log outputs\tests\tracking-tests.txt -- -m unittest test_cctv_track -v
```

## Nối track hậu xử lý và overlay tùy chọn

Tracking gốc vẫn reset ByteTrack theo segment và giữ nguyên tuple ID local.
Stage CPU hậu xử lý chỉ đọc publication tracking đã hoàn tất, tạo output riêng:

```powershell
.\.venv\Scripts\python.exe -m pipeline.cctv.stitch `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output-dir outputs\demo-data\stitched-clips `
  --max-gap-sec 1.0 --max-distance-diagonals 2.5

.\.venv\Scripts\python.exe -m pipeline.cctv.tracking_overlay `
  --manifest configs\cctv.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --stitched-dir outputs\demo-data\stitched-clips `
  --output outputs\demo-data\stitched-overlays --width 960
```

`stitched_tracks.jsonl` giữ observations gốc và bổ sung `global_track_id`;
`stitched_mapping.json` ánh xạ đầy đủ
`(run_id, camera_id, video_id, segment_id, track_id)` sang ID số dẫn xuất.
`stitch_state.json` niêm phong hashes artifacts, input tracking, code và settings.
Không sửa raw video, ledger tracking hay ID local. Chỉ nối giữa các segment
kề nhau trong cùng run/camera/video, không nối giữa camera hoặc video.

Nối dựa trên PTS thật, cửa sổ endpoint một giây, vận tốc từ tối đa ba mẫu,
residual dự đoán chuẩn hóa theo đường chéo bbox, class, diện tích và aspect.
Assignment có lựa chọn unmatched; cặp không hợp lệ và liên kết mơ hồ bị loại.
Đây là **giả thuyết hình học, chưa phải re-identification đã kiểm chứng**:
che khuất, đổi hướng, nhầm car/truck hoặc xe gần nhau vẫn có thể gây split/link sai.
Không dùng số global ID làm số xe thật hoặc bằng chứng IDF1/HOTA; stage này
không thay đổi continuity của congestion/incident analytics.

Không truyền `--stitched-dir` thì overlay vẫn hiện ID local như trước.
Khi bật, nhãn `GLOBAL`, màu theo global ID và banner `GEOMETRIC STITCHED IDs /
NOT VALIDATED RE-ID / ROI PENDING` phân biệt rõ output dẫn xuất;
`id_scope=derived_global_track_id/geometric_hypothesis`.
Mapping thiếu, khác run/input, publication chưa hoàn tất hoặc bị sửa đều báo lỗi,
không tự rơi về ID local. Overlay vẫn H264 CPU và giữ timeline native PTS.
Resume ràng buộc cả code stitch và artifacts đã niêm phong; đổi chế độ,
settings hoặc inputs cần output mới. Không đặt output overlay bên trong
tracking, stitching, raw hoặc source/config package.

Để phục vụ overlay dẫn xuất trong UI, cấu hình cả hai thư mục trước khi khởi động:

```powershell
$env:CCTV_OVERLAY_DIR = 'outputs\demo-data\stitched-overlays'
$env:CCTV_STITCHED_DIR = 'outputs\demo-data\stitched-clips'
.\scripts\Start-CctvDemo.ps1
```

HTTP store chỉ đọc mapping từ thư mục cấu hình tường minh, không tin đường dẫn
tùy ý trong checkpoint. Artifact/mapping bị sửa làm overlay unavailable;
retrieval và traffic độc lập vẫn hoạt động. Response giữ `id_scope` để UI
phân biệt ID local và giả thuyết nối hình học.


## Bằng chứng demo ngày 2026-10-01

Đã chạy 10 giây `[30,40)` trên mỗi video trong sáu MOV, 12 segment 5 giây:
**1.501 frame**, **31.065 observations**, không lỗi xử lý. Có 1.001 ID theo
scope segment, trong đó 915 có nhiều hơn một observation; đây không phải số
phương tiện thực tế. PyTorch peak allocated 99.85 MiB, reserved 130 MiB;
không bao gồm toàn bộ bộ nhớ CUDA driver hoặc tiến trình khác.

Đã đối chiếu PTS và thứ tự frame liên tiếp, join đúng 120 locator keyframe cũ,
kiểm tra sáu raw SHA256/mtime không đổi. Retrieval artifacts và detections
T3.2 giữ nguyên. Resume skip 1.501 frame, model_loaded=false, hash/mtime 18
artifacts/state/log giữ nguyên. Báo cáo cục bộ:
`outputs/demo-data-review/tracking-verification.json`,
`outputs/demo-data-review/detection-verification.json`.

74 core tests đạt trên Windows PowerShell 5.1, runtime FP16/NMS đạt; gồm 11
tests tracking về replay, frame lỗi, PTS thật, scope ID, publication recovery,
corruption guard và ByteTrack thật trên CPU. Không đồng nghĩa full legacy
suite hoặc độ chính xác tracking đã được nghiệm thu. Overlay cho thấy nhầm
car/truck, false positives và ID phân mảnh, đặc biệt xe nhỏ/che khuất; cần nhãn
track và đánh giá IDF1/HOTA trước khi dùng ID để đếm lưu lượng.

Tiếp theo T3.4: ROI có review cho N001/N002, occupancy và chuyển động ảnh
theo cửa sổ, persistence và hiệu chỉnh theo nhãn. Chưa có tốc độ km/h, nhận
diện tai nạn hoặc khuyến nghị điều phối nhân lực thật.

## Git

```powershell
git add pipeline/cctv/track.py configs/cctv.bytetrack.yaml test_cctv_track.py `
  requirements/windows-vision.txt requirements/windows-py311-cu126.lock.txt `
  scripts/Test-Windows.ps1 docs/CCTV_TRACKING.md docs/CCTV_PROGRESS.md README.md
git commit -m 'feat: add resumable ByteTrack tracking with native video timestamps'
git push origin main
```

Video, weights, JSONL, SQLite và previews trong outputs không được commit.
