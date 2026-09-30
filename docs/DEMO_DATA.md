# Dataset demo giao thông

## Nguồn và thiết kế

Ngày kiểm kê: 2026-09-30. Nguồn chỉ đọc: `D:\Project ExploreCS\Demo_Data`.
Người dùng xác nhận tiền tố N001/N002 đại diện cho hai camera; mỗi camera có ba video.
Manifest `configs/cctv.demo.json` dùng raw root tương đối `../../Demo_Data`, tính từ thư mục configs.
ID video giữ nguyên tên nguồn để liên kết keyframes và map theo legacy.
Chưa khai báo start_time, zone hoặc ROI: timestamp trên ảnh chưa được xác minh timezone;
vị trí địa lý và vùng đường cần được xác nhận trước module phân bổ nhân lực.

| Video | Thời lượng (giây) | Codec | Độ phân giải |
| --- | ---: | --- | --- |
| N001-V001 | 585.968 | H.264 | 1920 x 1080 |
| N001-V002 | 601.049 | H.264 | 1920 x 1080 |
| N001-V003 | 620.051 | H.264 | 1920 x 1080 |
| N002-V001 | 601.052 | H.264 | 1920 x 1080 |
| N002-V002 | 586.058 | H.264 | 1920 x 1080 |
| N002-V003 | 619.982 | H.264 | 1920 x 1080 |

Tổng: 2,034,635,683 byte (khoảng 1.90 GiB), 3,614.16 giây (60.236 phút).
FPS trung bình gần 25; map dùng timestamp/ordinal giải mã thực thay vì suy từ FPS trung bình.
Ảnh mẫu ở giây 30 cho thấy cảnh giao thông ngày/đêm. Chưa có nhãn tai nạn hoặc tắc đường;
không dùng ảnh mẫu để kết luận sự kiện hoặc độ chính xác nhận diện.
FFmpeg báo metadata SEI bị truncated và có cảnh báo giải mã khi lấy ảnh mẫu;
pipeline giữ cơ chế phục hồi SEI legacy trong file tạm, không sửa nguồn.

## PowerShell chạy và kiểm tra

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m pipeline.cctv.manifest .\configs\cctv.demo.json --require-files
.\.venv\Scripts\python.exe scripts\run_python_logged.py --log outputs\demo-data-review\ingest-console.txt -- -m pipeline.cctv.ingest --manifest configs\cctv.demo.json --output-dir outputs\demo-data\processed --device cpu

# Resume: kiểm tra integrity, bỏ qua video đã hoàn tất
.\.venv\Scripts\python.exe scripts\run_python_logged.py --log outputs\demo-data-review\resume-console.txt -- -m pipeline.cctv.ingest --manifest configs\cctv.demo.json --output-dir outputs\demo-data\processed --device cpu
.\scripts\Test-Windows.ps1
```

Python logged runner giữ warning trong log và trả exit code thật, tương thích PowerShell 5.1.
Decoder CPU chạy tuần tự; stage này chưa nạp model hoặc dùng VRAM cho inference.
Khoảng 2 FPS giữ tương thích extraction legacy, phục vụ retrieval; chưa đủ làm detector
chuyển động/tai nạn cho mọi tình huống. Stage giao thông cần kiểm thử sampling riêng.

Output JPEG nằm tại `outputs\demo-data\processed\keyframes\<video_id>`;
map tại `map-keyframes\<video_id>.csv`, schema `n,pts_time,fps,frame_idx`.
Checkpoint và log ở cùng processed root. Chỉ dùng cùng manifest/recipe để resume;
đổi nội dung nguồn hoặc cấu hình camera cần output folder mới.
Output hiện chưa có CLIP embeddings, FAISS hoặc SQLite serving index (`serving_ready=false`).
Raw video, keyframes, ảnh mẫu và log đều không commit vào Git.

## Kết quả kiểm tra trên máy này

Extraction CPU hoàn tất 6/6 video, không có video lỗi. Tổng 7,232 JPEG:
N001-V001: 1,172; N001-V002: 1,203; N001-V003: 1,241;
N002-V001: 1,203; N002-V002: 1,173; N002-V003: 1,240.
Mỗi bộ ảnh đã được pipeline decode/validate và đối chiếu với CSV map trước khi publish.
Hash SHA-256 và mtime của sáu video gốc khớp trước/sau extraction.
Chạy lại trả `done=0, skipped=6, error=0`; kích thước và mtime của 7,232 JPEG
và sáu CSV map không đổi. Báo cáo resume: `outputs/demo-data-review/verification.json`.
Kiểm kê/hash cục bộ lưu trong `outputs/demo-data-review/source-inventory.json`.
Bộ `Test-Windows.ps1` qua 28 test và runtime CUDA FP16/NMS.
Thông báo `retrieval_baseline=NOT_TESTED_NO_DATA` trong runtime verifier hiện nói về
serving bundle; có raw video/keyframes không đồng nghĩa đã có index để benchmark retrieval.

## Git

```powershell
git diff --check
git add -- configs/cctv.demo.json docs/DEMO_DATA.md README_CCTV.md
git commit -m "feat: register two-camera traffic demo dataset"
git push origin main
```
