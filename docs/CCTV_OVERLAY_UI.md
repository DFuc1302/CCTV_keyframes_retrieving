# T9.3 — tracking overlay trong tab Giao thông

## Concept / architecture rationale

T9.2 xuất MP4 H.264 từ ledger; T9.3 thêm API chỉ đọc và nút **Xem bbox và ID
tracking** cho cửa sổ đang chọn. Không chạy tracker, nạp model thứ hai hoặc
ghi annotation/review. Missing overlay không tắt retrieval/traffic/video gốc.

`app/cctv_overlay.py` kiểm tra checkpoint complete, exact input allowlist,
manifest/tracking/raw SHA association, camera/video, frame count và PTS đầu
từ SQLite read-only. Không mở path tùy ý từ JSON. MP4 được rehash trước phục
vụ; source/input/checkpoint edits hoặc deletion invalidate snapshot. Restart
sau khi xác minh pipeline để nạp lại. Guards phát hiện ordinary edits, không
thay chữ ký chống giả mạo. Bounds: 4 MiB state, 24 clips, 5.000 frames/clip,
100 MiB/MP4.

| Route / field | Hành vi |
| --- | --- |
| `GET /cctv/traffic/overlays` | Optional readiness riêng, summaries và flags read-only/predictions |
| `GET /cctv/traffic/overlay/{video_id}` | MP4/Range, 404 ID lạ; 503 publication thiếu/đổi |
| Window `tracking_overlay` | Locator clip hoặc null nếu không khả dụng |
| Window `evidence` | Giữ nguyên locator/offset video gốc |

Overlay seek = source PTS − PTS frame đầu overlay; không dùng raw-preview
offset hoặc frame_idx/FPS. Caption player giữ timestamp nguồn/frame_idx,
banner ghi segment scope. Predictions, ID theo segment và ROI pending hiển
thị rõ; không diễn giải bbox/ID như nhãn tai nạn/tắc đường.

## Complete code / PowerShell run and test

Code đầy đủ: `app/cctv_overlay.py`, routes `app/cctv.py`, UI
`static/cctv/index.html`, `traffic.js`, `app.js`; tests
`test_cctv_overlay_api.py`. Default manifest `configs/cctv.demo.json`, clips
`outputs/demo-data/tracking-overlay-v2`; prepare theo `CCTV_TRACKING_OVERLAY.md`.
Dừng server cũ rồi khởi động lại để nạp code/assets mới:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Start-CctvDemo.ps1
```

Mở `http://127.0.0.1:8080`, chọn **Giao thông**, camera/video/cửa sổ, rồi
**Xem bbox và ID tracking**. Nút xem video gốc vẫn cạnh nút mới.
Server hỗ trợ `CCTV_OVERLAY_DIR` nếu dùng folder khác; frozen smoke bên dưới
là recipe mặc định v2, không dùng để nghiệm thu custom folder.

Khi chưa có server, tự start/check/stop:

```powershell
.\scripts\Test-CctvDemo.ps1 -RequireOverlays
```

Khi server đã chạy:

```powershell
.\scripts\Test-CctvDemo.ps1 -ExistingServer -Port 8080 -RequireOverlays
```

Report mới mỗi run. Flag yêu cầu overlay freeze package v2, kiểm tra sáu
Range responses và offset mọi traffic window. Tests dùng synthetic video/
encoder, không tải weights:

```powershell
& .\.venv\Scripts\python.exe scripts/run_python_logged.py `
  --log outputs/tests/overlay-api.txt -- -m unittest -v test_cctv_overlay_api
Get-Content outputs/tests/overlay-api.txt -Tail 12
.\scripts\Test-Windows.ps1
```

## Verification / Git

Windows core suite đạt **259 tests**, pip check/compile/CUDA FP16/NMS đạt.
7 tests mới kiểm tra Range/offset/read-only, missing package, edit guards,
flags, arbitrary input paths và snapshot invalidation. Real HTTP smoke trên
8087 với RequireOverlays đạt sáu clips/30 windows, 108 protected files giữ
SHA/mtime. Report `outputs/demo-data/acceptance-overlay-t9-v1/acceptance_report.json`.
Browser N001-V001 source 30,037 → overlay 0,000; source 36,036 → overlay
5,999 giây, readyState=4, không media error/console warn/error. Đây là kiểm
tra playback cục bộ, không là đo chất lượng semantic.

API composition đã thay đổi: latency T6.2/T7.3 là báo cáo lịch sử gắn code
cũ, không là phép đo mới của phiên này; không sửa reports/checkpoints cũ.
Video/notes/outputs không đưa lên GitHub:

```powershell
git add app/cctv.py app/cctv_overlay.py static/cctv/app.js static/cctv/index.html static/cctv/traffic.js test_cctv_overlay_api.py scripts/Test-Windows.ps1 scripts/Test-CctvDemo.ps1 scripts/run_cctv_demo_smoke.py scripts/verify_cctv_demo.py docs/CCTV_OVERLAY_UI.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md
git commit -m "feat: serve tracking overlays in the traffic demo"
git push origin main
```
