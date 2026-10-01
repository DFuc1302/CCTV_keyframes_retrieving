# T9.2 — xuất video tracking overlay trên CPU

## Concept / architecture rationale

T3.3 lưu tracking ledger/frame PTS và ảnh cuối segment; chưa có video thể hiện
bbox/ID chuyển động. `pipeline/cctv/tracking_overlay.py` bổ sung MP4 H.264
để xem baseline đã lưu. Không chạy lại YOLO/ByteTrack, không nạp weights hoặc
gán nhãn sự kiện. Các outputs tracking/CLIP/index cũ giữ nguyên.

Module kiểm tra track checkpoint hoàn chỉnh, SHA của database/ledgers/manifest,
nguồn camera/video và raw video. SQLite được mở `mode=ro`. Decode từ origin
như tracker, ghép chính xác frame_idx, source_pts, origin_pts, time_base,
timestamp, run, camera/video và kích thước. Sai locator/thiếu frame chặn clip;
không bỏ qua frame lỗi rồi công bố video thiếu bằng chứng.

Mỗi frame được resize xuống tối đa width đã chọn, vẽ bbox/ID từ ledger và
banner source PTS + segment. ID có scope run/camera/video/segment; các segment
có thể tái sử dụng ID, không tính chúng là xe duy nhất xuyên segment/video.
Banner luôn ghi predictions/ROI pending, kể cả tracking readiness đạt.

Encoder libx264 dùng CPU, yuv420p, hai threads và một frame mỗi lượt. PTS
output bằng `(source_pts - first_source_pts) * time_base`; không tái dựng
timestamp từ frame_idx hoặc constant FPS. Rate encoder 25 chỉ là cấu hình
codec, không thay PTS được truyền vào. Sau encode, decode toàn bộ MP4 và đối
chiếu mọi PTS với ledger (sai số tối đa 1 microsecond). Playback t=0 tương ứng
frame đầu được decode trong khoảng; offset thật lưu ở checkpoint, có thể khác
start_sec được yêu cầu. Không thêm audio.

## Complete executable code / PowerShell run and test

Code đầy đủ `pipeline/cctv/tracking_overlay.py`, fixtures
`test_cctv_tracking_overlay.py`. Chạy với tracking clips đã publish:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
& .\.venv\Scripts\python.exe scripts/run_python_logged.py `
  --log outputs/tests/tracking-overlay-console.txt -- `
  -m pipeline.cctv.tracking_overlay `
  --manifest configs/cctv.demo.json `
  --tracking-dir outputs/demo-data/tracking-clips `
  --output outputs/demo-data/tracking-overlay-v2 `
  --width 960
Get-Content outputs/tests/tracking-overlay-console.txt -Tail 20
Get-Content outputs/demo-data/tracking-overlay-v2/overlay_state.json
.\scripts\Test-Windows.ps1
```

MP4s nằm trong output theo tên video nguồn. Clip tối đa 60 giây, 2..5.000
frames/video, tối đa 24 video, width chẵn 320..1920; đây là bounded preview,
không phải export cả giờ dữ liệu. Các clip demo hiện tại là khoảng `[30,40)`.
Không tăng limits để xử lý toàn bộ CCTV khi chưa có thiết kế tiếp theo.

`tqdm` hiển thị tiến độ từng video; lỗi được log theo frame và video. Video
đã verify mới được chuyển từ partial sang MP4 cuối và checkpoint. Lỗi một
video không chặn thử các video khác; job tổng vẫn trả nonzero và incomplete.
Resume kiểm tra input/recipe SHA và SHA từng MP4, skip video đã hoàn tất mà
không decode/encode lại, giữ bytes/mtime. File output chỉnh/mất không bị ghi
đè. Partial chưa được checkpoint được giữ để kiểm tra; chọn output mới khi
cần chạy lại clip đó. Thay source/tracking/code/width cũng cần output mới.

Raw chỉ đọc; output không được trùng raw/config/tracking/code. Module không
đọc review journal, không phê duyệt ROI hoặc tạo annotations. Overlay chứa
predictions: không dùng làm tư liệu blind labeling T8.1.

## Verification và Git

Tests sử dụng synthetic tracking: H.264/PTS thực, frame count, camera/PTS/ledger
mismatch, thiếu frame, raw checksum, resume và edited/source/foreign guards.
Video thật chỉ kiểm tra khớp dữ liệu đã lưu, chưa đo độ chính xác tracking.

Windows core suite đạt 252 tests, pip check/compile và CUDA FP16/NMS đạt.
Demo xuất sáu MP4/1.501 frames tại `tracking-overlay-v2`, mọi PTS được verify.
Resume skip sáu video mà không gọi decoder; 26 source/artifact files giữ
SHA/mtime, sáu raw videos và tracking publication không đổi. Ảnh QA từ MP4
cho thấy bbox/ID và banner scope/PTS/pending hiển thị; không chấm accuracy.
Không suy tai nạn/tắc đường hoặc hiệu chuẩn tốc độ từ hình bbox/ID.

```powershell
git add pipeline/cctv/tracking_overlay.py test_cctv_tracking_overlay.py scripts/Test-Windows.ps1 docs/CCTV_TRACKING_OVERLAY.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md
git commit -m "feat: export source-aligned tracking overlay clips on CPU"
git push origin main
```

Đây là bước export offline. Nối video vào UI traffic là task tiếp theo riêng;
ROI, nhãn chất lượng và máy thứ hai vẫn chưa nghiệm thu.
