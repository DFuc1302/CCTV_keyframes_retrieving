# M1 — manifest và pipeline tiếp nhận CCTV

## Concept và kiến trúc

Manifest tách ID video/camera khỏi tên file gốc, để tên bất kỳ, dấu tiếng Việt và khoảng trắng không làm mất ánh xạ. Đây là manifest CCTV mới, không thay thế manifest phân shard của legacy.

`pipeline/cctv/manifest.py` kiểm tra schema, ID không trùng theo kiểu Windows, camera tham chiếu, timestamp có timezone, ROI chuẩn hóa và path không thoát raw root. `start_time` là tùy chọn; không suy thời gian thực từ tên file. ROI là metadata cho stage giao thông sau, chưa được dùng để nhận diện.

`pipeline/cctv/ingest.py` điều phối từng video tuần tự và tái sử dụng `pipeline.frames.extract.extract_one`, gồm cơ chế phục hồi SEI của legacy. Quy tắc lấy mẫu vẫn khoảng 2 FPS và map vẫn `n,pts_time,fps,frame_idx`. `pts_time` tương đối với frame giải mã đầu tiên; không thay bằng số ảnh chia FPS.

Không cần dataset cũ, tên S01, model, CUDA hay old index. Decoder mặc định CPU; chưa có inference nên chưa sử dụng batch/FP16 cho model. `--device cuda` chỉ đổi decoder FFmpeg, không phải stage CLIP.

Shared `output_lease` hỗ trợ Windows byte-range lock và POSIX `flock`.
Cả hai nonblocking, chặn writer thứ hai và được OS giải phóng khi process
kết thúc. Smoke Colab Linux và regression Windows đã kiểm tra cạnh tranh/
release sau owner exit ngày 2026-10-02; xem
[Colab portability](CCTV_FULLVIDEO_COLAB.md#code-identity-windows--linux--2026-10-02).

## Hợp đồng manifest

`configs/cctv.example.json` là scaffold không có video; có thể kiểm tra cấu trúc nhưng không chạy extraction. Ví dụ một bản đã đăng ký video:

```json
{
  "schema_version": 1,
  "raw_root": "D:/CCTV/raw",
  "cameras": [
    {"camera_id": "CAM_001", "name": "Camera 1", "zone": "ZONE_001"}
  ],
  "videos": [
    {"video_id": "ROAD_001", "camera_id": "CAM_001", "path": "camera 1/road clip.mp4"}
  ]
}
```

Đây là minh họa schema; chỉ chạy khi các file đăng ký thực sự tồn tại. `raw_root` tương đối được resolve từ folder chứa manifest. `path` của video luôn tương đối trong raw root. `video_id`/`camera_id` dùng ASCII để tên output ổn định; tên file nguồn được giữ nguyên và có thể là Unicode.

Có thể thêm `start_time` như `2026-09-30T12:00:00+07:00` khi biết thời gian thực và `roi` gồm ít nhất ba điểm `[x,y]` trong `[0,1]`, có diện tích khác 0. ROI chưa kiểm tra đầy đủ topology hay hiệu chuẩn camera.

## Chạy trên Windows với fixture không cần CCTV

Từ root repo:

```powershell
.\scripts\New-CctvDemo.ps1
.\.venv\Scripts\python.exe -m pipeline.cctv.manifest .\outputs\cctv-demo\manifest.json --require-files
.\.venv\Scripts\python.exe -m pipeline.cctv.ingest --manifest .\outputs\cctv-demo\manifest.json --output-dir .\outputs\cctv-demo\processed --device cpu

# Chạy lại: kiểm tra integrity rồi skip video đã hoàn tất
.\.venv\Scripts\python.exe -m pipeline.cctv.ingest --manifest .\outputs\cctv-demo\manifest.json --output-dir .\outputs\cctv-demo\processed --device cpu
```

Demo tạo video test pattern 1,6 giây, không phải cảnh giao thông và không dùng đánh giá nhận diện. Generator từ chối ghi đè fixture; lần sau dùng `-DemoName cctv-demo-02` để tạo demo mới. Pipeline chỉ đọc fixture đầu vào.

Kiểm tra scaffold khi chưa có data:

```powershell
.\.venv\Scripts\python.exe -m pipeline.cctv.manifest .\configs\cctv.example.json
```

## Output, resume và data safety

```text
processed/
  keyframes/<video_id>/001.jpg ...
  map-keyframes/<video_id>.csv
  dataset_manifest.json
  ingest_state.json
  logs/ingest.log
  .staging/
  .ingest.lock
```

- Output và raw root phải tách rời, không lồng nhau. Source manifest cũng nằm ngoài processed output. Không đổi video nguồn hoặc dataset/keyframe legacy.
- Hash SHA-256 của nguồn, camera/time association, recipe và output xác minh resume. Thay source hoặc recipe phải dùng output folder mới. Resume đọc/hash video và kiểm tra JPEG nên có chi phí I/O; chưa phải tối ưu cho kho cực lớn.
- Lock Windows ngăn hai writer cùng output; OS giải phóng lock khi process dừng. `.ingest.lock` có thể còn tồn tại, không cần xóa để resume.
- Stage riêng chỉ publish ảnh/map đã validate. Nếu dừng giữa publish ảnh và map, chạy lại sẽ đối chiếu nội dung rồi publish phần thiếu, không ghi lại ảnh đã khớp.
- Output không có checkpoint sở hữu, ảnh hỏng hoặc output đã thay đổi sẽ bị từ chối, giữ nguyên để kiểm tra. Không tự xóa dữ liệu để sửa lỗi.
- Lỗi từng frame được log trong validation; lỗi từng video không chặn video tiếp theo. Exit code 1 khi có lỗi; Ctrl+C trả 130 và giữ checkpoint cho lần sau.
- `dataset_manifest.json` ghi trạng thái từng video. `serving_ready=false`: đây chưa phải serving bundle, không có embeddings/FAISS/SQLite index.

## Verification và Git

```powershell
.\.venv\Scripts\python.exe -m unittest test_cctv_manifest test_cctv_ingestion -v
.\scripts\Test-Windows.ps1
git diff --check
git diff
git add -- pipeline/cctv configs/cctv.example.json test_cctv_manifest.py test_cctv_ingestion.py scripts/New-CctvDemo.ps1 scripts/Test-Windows.ps1 docs/CCTV_INGESTION.md
git diff --cached --check
git commit -m "feat: add resumable manifest driven CCTV ingestion"
git push origin main
```

Tests kiểm tra timestamp thực, raw hash không đổi, skip không ghi lại ảnh, interruption/resume, output bị hỏng, nguồn/recipe thay đổi, lỗi video độc lập, traversal/ID sai, overlap và lock writer. Các test chỉ tạo và sửa fixture tổng hợp trong thư mục tạm.

Kết quả kiểm tra M1: 6 test manifest và 10 test ingestion, tổng 16 test. Bộ core Windows có thêm 10 test kế thừa, tổng 26 test. Demo CLI tạo 4 JPEG ở 0/0,5/1/1,5 giây; chạy lại skip 1 video. Chưa dùng kết quả này làm benchmark retrieval hoặc phát hiện sự kiện.

Bước tiếp theo: CLIP embedding, metadata SQLite và bundle FAISS cho dataset mới; sau đó mới nghiệm thu retrieval trên video CCTV thật.
