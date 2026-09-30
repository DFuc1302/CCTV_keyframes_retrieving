# T3.5 — API/UI giao thông trong demo CCTV

## Khái niệm và kiến trúc

`app/cctv_traffic.py` phục vụ một snapshot read-only từ output T3.3/T3.4 và
ảnh ROI. `app/cctv.py` ghép module này vào demo hiện có; không sửa legacy
`app/main.py`, schemas extraction hoặc index. Một CLIP instance vẫn phục vụ
text/image, không nạp YOLO để xem traffic. Thống kê đọc SQLite URI `mode=ro`.

Startup xác minh checksum artifacts, signature traffic/tracking, ROI preview,
source SHA khớp video của retrieval, camera/video/run và phạm vi clip. Hai
ledger windows/signals phải khớp từng record. Evidence lấy frame đầu trong
window từ tracks.db và cộng seek_offset_sec MP4 đã có. Ảnh ROI lấy frame cuối
segment tham chiếu: UI ghi rõ timestamp cố định, không trình bày như overlay
video trực tiếp. Không suy vị trí hay thời gian ghi hình tuyệt đối.

Readiness giao thông độc lập: thiếu/hỏng output không chặn retrieval hoặc
nạp thêm model. `/ready` tiếp tục trả 200 khi retrieval đạt; capability traffic
và traffic.available cho biết dữ liệu giao thông khả dụng. operational_ready
và thresholds_calibrated luôn false trong baseline hiện tại, kể cả ROI đã duyệt.

Snapshot giới hạn 10.000 windows tổng và mỗi ledger tối đa 64 MiB. API phân
trang tối đa 500 rows; UI dùng 40 cửa sổ/trang. Thay đổi/xóa file thông thường
được phát hiện qua size/mtime/ctime và traffic chuyển unavailable. PNG nhỏ
được rehash trước serve. Sau khi chạy pipeline mới, khởi động lại server để
checksum toàn bộ và nạp snapshot mới; nút Kiểm tra lại chỉ đọc trạng thái,
không chạy pipeline hoặc reload model.

## Code và API

Code đầy đủ: `app/cctv_traffic.py`, tích hợp tại `app/cctv.py`, UI tại
`static/cctv/index.html`, `traffic.js`, `style.css`.

| Endpoint | Hành vi |
| --- | --- |
| `GET /cctv/traffic` | 200 cùng available, cờ demo/readiness, video catalog và URL ROI; không lộ đường dẫn local |
| `GET /cctv/traffic/windows/{video_id}?offset=0&limit=200` | Windows/signals đã validate, total/pagination và evidence frame/seek URL |
| `GET /cctv/traffic/roi/{video_id}` | PNG ROI được đăng ký và xác minh checksum |
| `GET /ready` | Capabilities retrieval + traffic riêng; thiếu traffic không làm retrieval unready |

Windows/ROI trả 503 khi traffic không khả dụng, 404 cho ID ngoài catalog,
422 cho offset âm hoặc limit ngoài 1–500. Không nhận filesystem path từ query.
Các thông báo HTTP không chứa absolute paths của traffic artifacts.

Tab **Giao thông** có lọc camera/video, ROI tham chiếu, bốn chỉ số (count,
occupancy, motion, coverage), timeline occupancy và bảng từng cửa sổ.
Chọn cột cập nhật chỉ số; chọn thời gian trong bảng hoặc nút Xem cửa sổ mở
MP4 ở frame chứng cứ. Motion null hiển thị Chưa đủ dữ liệu, không hiển thị 0.
Request đổi camera/video dùng token để bỏ response cũ; tạm chặn mở evidence
cũ khi request mới đang tải. Tab hỗ trợ phím mũi tên và các nút timeline dùng
keyboard. Trên mobile, bảng cuộn ngang bên trong, trang không tràn ngang.

## Chạy và kiểm thử PowerShell

Giả định các stage M2, T3.3 và T3.4 demo đã chạy. Mặc định:

- `CCTV_TRAFFIC_DIR`: `outputs/demo-data/traffic-draft-v1`
- `CCTV_TRACKING_DIR`: `outputs/demo-data/tracking-clips`
- `CCTV_ROI_PREVIEW_DIR`: `outputs/demo-data/roi-draft-previews`

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
# Chạy trước server để tránh hai stage giữ GPU/RAM đồng thời.
.\scripts\Test-Windows.ps1
.\scripts\Start-CctvDemo.ps1
```

Mở `http://127.0.0.1:8080`, chọn Giao thông. Nếu demo đang chạy sẵn, dùng
tab hiện có; để cập nhật process do mình chạy trong terminal, Ctrl+C rồi
khởi động lại. Không chạy nhiều CLIP server hoặc thêm workers/reload trên 8GB.

Terminal thứ hai:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8080/cctv/traffic'
Invoke-RestMethod 'http://127.0.0.1:8080/cctv/traffic/windows/N002-V003?offset=0&limit=5'
Invoke-RestMethod 'http://127.0.0.1:8080/ready'
```

Output khác dùng đường dẫn tuyệt đối, thiết lập trước khi start:

```powershell
$env:CCTV_TRAFFIC_DIR = 'D:\Project ExploreCS\CCTV_keyframes_retrieving\outputs\demo-data\traffic-draft-v1'
$env:CCTV_TRACKING_DIR = 'D:\Project ExploreCS\CCTV_keyframes_retrieving\outputs\demo-data\tracking-clips'
$env:CCTV_ROI_PREVIEW_DIR = 'D:\Project ExploreCS\CCTV_keyframes_retrieving\outputs\demo-data\roi-draft-previews'
.\scripts\Start-CctvDemo.ps1
```

## Nghiệm thu 2026-10-01

**100 core tests đạt** trên Windows PowerShell 5.1, runtime CUDA FP16/NMS đạt.
Tám tests mới kiểm tra read-only/shared encoder, pagination, source/ROI
provenance, missing/corrupt artifacts, live edit invalidation, asset confinement
và cờ demo/evidence. Không đồng nghĩa full legacy suite đạt hoặc độ chính xác
congestion đã được nghiệm thu.

HTTP thật: 30 windows của sáu video khớp ledger T3.4, sáu PNG trả đúng bytes,
video range trả 206, text retrieval trả kết quả bình thường. Trình duyệt thật
kiểm tra tab, lọc N002, đổi video và camera nhanh, chọn timeline, text query.
Cửa sổ N002-V003 `[34,36)` mở frame 850 tại 34.001 giây; video decode
1920×1080, paused và không lỗi. Viewport 390×844: page width 375 (phần còn lại
là scrollbar), bảng cuộn trong vùng 334 px, không tràn ngang toàn trang.
Không có console error/warn ở lần kiểm tra cuối.

Raw SHA/mtime và các artifacts retrieval/detection/tracking/windows giữ nguyên.
Bằng chứng cục bộ: `outputs/demo-data-review/traffic-api-verification.json`,
`traffic-ui-desktop.jpg`, `traffic-ui-mobile.jpg`, `traffic-ui-video.jpg`, cùng
reports hash/resume các stage trước. Các files này không push GitHub.

M3 có baseline chạy thử từ detection đến API/UI; **review ROI, calibration và
nhãn đánh giá còn mở**. Task tiếp theo T4.1: event schema và evidence/human
review cho tình huống nghi sự cố; không gọi congestion rule hiện tại là mô
hình nhận diện tai nạn. Camera location và nguồn lực vẫn chưa đủ để điều phối
cảnh sát thực tế.

## Git

```powershell
git add app/cctv.py app/cctv_traffic.py static/cctv/index.html `
  static/cctv/traffic.js static/cctv/style.css test_cctv_traffic_api.py `
  scripts/Test-Windows.ps1 docs/CCTV_TRAFFIC_UI.md docs/CCTV_PROGRESS.md README_CCTV.md README.md
git commit -m 'feat: expose CCTV traffic windows and evidence timeline in demo UI'
git push origin main
```
