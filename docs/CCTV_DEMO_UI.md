# M2 — Demo CCTV trên FastAPI

## Concept và kiến trúc

`app/cctv.py` cung cấp composition FastAPI riêng, tái sử dụng `ClipEncoder` và
`CctvSearch` cùng database/vector/locator legacy. Không sửa `app/main.py` hoặc
database để khởi động OCR/ASR của legacy. `static/cctv/` là giao diện tìm kiếm
text/image và xem video tại timestamp của kết quả.

Một worker sở hữu một instance CLIP dùng chung cho text và image, CUDA FP16,
khóa tuần tự inference để tránh nhiều request tăng VRAM đồng thời. Ảnh query
chỉ decode trong RAM, tối đa 8 MiB và 16 megapixels; không lưu hoặc gửi ra ngoài.
Score cosine là độ tương đồng, chưa phải xác suất tai nạn/tắc đường. Query text
tiếng Anh phù hợp baseline CLIP hiện có.

`pipeline/cctv/preview.py` remux H.264 MOV thành MP4 bằng stream copy trong output
riêng; không encode lại hoặc sửa video gốc. `+faststart` và HTTP byte range hỗ trợ
trình duyệt phát/seek video. Chỉ giữ video track, bỏ audio/subtitle/data khỏi bản
preview. Checkpoint ghi hash nguồn/output và PTS đầu; API cộng PTS đầu preview
vào timestamp tương đối của keyframe. UI mở video ở trạng thái paused để kiểm
tra khoảnh khắc rồi người dùng chọn Play. Seek của trình duyệt không bảo đảm
frame accuracy; timestamp và decoded frame index được giữ để đối chiếu.

Preview chạy từng video với tqdm/log, checkpoint và khóa output. Gián đoạn có
thể chạy lại; file đã hoàn tất và khớp checksum được bỏ qua. File lạ hoặc thay
đổi được giữ nguyên và trả lỗi; input thay đổi phải dùng output folder mới.

Startup kiểm tra checksum index, recipe model, coverage metadata, keyframe paths,
hash video nguồn và MP4 trước khi chạy query CLIP thật. `/ready` trả 200 chỉ khi
text/image/video cùng sẵn sàng; lỗi trả 503 kèm chẩn đoán. `/health` chỉ báo process
đang chạy. `serving_ready=false` trong index manifest vẫn thuộc hợp đồng serving
legacy, không được sửa để giả lập readiness của API mới.

## Chạy và kiểm thử bằng PowerShell

Giả định extraction/index/cache đã hoàn tất theo `CCTV_RETRIEVAL.md`; FFmpeg và
ffprobe có trong PATH. Mặc định đọc các output hiện tại của demo.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'

# Chạy trước khi nạp model server; chạy lại cùng lệnh để resume
.\.venv\Scripts\python.exe scripts\run_python_logged.py --log outputs\demo-data-review\preview-console.txt -- -u -m pipeline.cctv.preview --index-dir outputs\demo-data\search-index --output-dir outputs\demo-data\video-preview
if ($LASTEXITCODE -ne 0) { throw 'Preview failed; inspect the log.' }

.\scripts\Start-CctvDemo.ps1
# Mở http://127.0.0.1:8080 ; Ctrl+C dừng server
```

Terminal PowerShell thứ hai:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8080/ready'
Invoke-RestMethod 'http://127.0.0.1:8080/cctv/search?query=road%20intersection%20at%20night&top_k=6'

$sampleImage = 'D:\Project ExploreCS\CCTV_keyframes_retrieving\outputs\demo-data\processed\keyframes\N001-V001\500.jpg'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8080/cctv/search/image?top_k=6' -ContentType 'image/jpeg' -InFile $sampleImage
```

Trên UI: nhập mô tả, chọn số kết quả và tìm kiếm; chuyển sang Ảnh mẫu để chọn ảnh
JPEG/PNG/WebP. Click keyframe để mở video đúng camera/video/time; Escape đóng.
Điều khiển tab hỗ trợ phím mũi tên; layout thu về một cột trên màn hình nhỏ.

```powershell
# Dừng server bằng Ctrl+C trước khi chạy suite để hạn chế bộ nhớ đồng thời
.\scripts\Test-Windows.ps1

# Đổi port hoặc chạy CPU khi cần (chậm hơn)
.\scripts\Start-CctvDemo.ps1 -Port 8081 -Device cpu
```

Không dùng reload hoặc nhiều workers trên GPU 8GB. Launcher bind localhost, không
đổi execution policy hệ thống. Có thể cấu hình `$env:CCTV_INDEX_DIR`,
`$env:CCTV_MODEL_CONFIG`, `$env:CCTV_PREVIEW_DIR` bằng đường dẫn tuyệt đối.

## Nghiệm thu ngày 2026-09-30

- Core suite: **45 tests đạt** trên Windows PowerShell 5.1; runtime CUDA FP16/NMS đạt.
  Năm API tests kiểm tra shared model, readiness lỗi, upload bounds, asset lookup
  và range; ba preview tests dùng MOV tổng hợp với FFmpeg thật để kiểm tra timestamp,
  resume, publication gián đoạn và bảo toàn file hỏng.
- Dữ liệu thật: `/ready` 200, 7.232 frames, hai camera N001/N002, đủ 6 MP4.
  Preview resume: `done=0, skipped=6, error=0`, hash/mtime MP4 không đổi.
- Tìm text và image trên trình duyệt thật; ảnh `N001-V001/500.jpg` trả chính nó
  top 1, score 1,000, timestamp 249,519 giây, decoded frame index 6238.
- Video trong trình duyệt decode 1920×1080, paused tại 249,519 giây không lỗi;
  range request thật trả 206. Viewport 390×844 không tràn ngang.
- Cả sáu video gốc giữ nguyên hash/mtime; bốn artifacts publication index giữ
  checksum sau preview và HTTP queries. Không commit footage, model hoặc outputs.

Báo cáo cục bộ: `outputs/demo-data-review/ui-verification.json`, ảnh nghiệm thu
`outputs/demo-data-review/ui-demo.jpg`, log suite `outputs/tests/core-tests.txt`.
Các kiểm tra trên là smoke tests, không thay benchmark Recall@K/mAP. Nhận diện
tai nạn, tracking/mật độ, ROI và phân bổ nhân lực chưa được triển khai trong bước này.

## Git

```powershell
git diff --check
git add -- app/cctv.py pipeline/cctv/preview.py static/cctv scripts/Start-CctvDemo.ps1 scripts/Test-Windows.ps1 test_cctv_api.py test_cctv_preview.py docs/CCTV_DEMO_UI.md docs/CCTV_RETRIEVAL.md README_CCTV.md
git diff --cached --check
git commit -m "feat: add local CCTV search demo with video previews"
git push origin main
```
