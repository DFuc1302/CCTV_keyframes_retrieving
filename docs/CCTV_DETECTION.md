# T3.2 — Baseline phát hiện xe trên keyframes

## Concept và kiến trúc

Giữ YOLOv8m và `imgsz=640` của nhánh YOLO legacy, tách stage CCTV khỏi OCR/CLIP
để không nạp nhiều model lớn cùng lúc. Trọng số từ release Ultralytics `v8.3.0`,
SHA-256 được pin trong downloader và descriptor cache. Inference chỉ đọc file
local đã khớp checksum, không tự tải model hoặc cài package.
Nguồn: [YOLOv8 chính thức](https://docs.ultralytics.com/models/yolov8/),
[Predict API](https://docs.ultralytics.com/modes/predict/).

`pipeline/cctv/detect.py` tái sử dụng `collect_frames` để kiểm tra dataset ingestion
đã hoàn tất, hash ảnh/map và locator legacy. Detections giữ schema T3.1, không thêm
bảng vào `meta.db` hoặc thay vector CLIP. Phạm vi hiện tại là keyframes khoảng
2 FPS; không phải tracking liên tục, đếm xe đi qua hoặc phân loại tai nạn.

Baseline chọn COCO IDs 1/2/3/5/7 (bicycle/car/motorcycle/bus/truck). Confidence
0,25, NMS IoU 0,7, max_det 300 là cấu hình khởi đầu được ghi trong recipe,
chưa tối ưu bằng nhãn CCTV. Confidence không phải xác suất tai nạn. NMS giảm
box trùng; ngưỡng cần đánh giá precision/recall trước khi dùng trong thống kê.
640 px giảm chi phí nhưng có thể bỏ sót xe nhỏ ở xa hoặc thiếu sáng.

CUDA dùng FP16, `torch.amp.autocast` và inference mode. SDK hiện tại dùng
`quantize=16`; bản Ultralytics cũ không có tham số này dùng `half=True`.
Batch mặc định 2, cho phép 1–4; lỗi batch được log và chia đôi để cô lập từng ảnh,
có `empty_cache` trước retry trên CUDA. Một ảnh vẫn lỗi được ghi vào checkpoint;
không công bố JSONL hoàn tất khi còn lỗi. Model được GC/empty_cache khi kết thúc.

Ảnh được decode từng batch bằng PIL; boxes normalize về ảnh gốc, không phải
ảnh letterbox 640. Mỗi record hợp lệ được commit trong một SQLite transaction.
Gián đoạn giữ các frame đã commit; chạy lại skip đúng frame đó, kể cả frame
không có detection. Checkpoint kiểm tra hash record và locator trước resume.

Recipe gồm hash toàn bộ input inventory, traffic config, weights, code/contract,
phiên bản SDK/torch/Pillow và các tham số. Đổi recipe hoặc input phải dùng output
mới. Output khóa riêng; folder/file không thuộc pipeline hoặc checksum bị đổi
được giữ nguyên và trả lỗi. JSONL/manifest/previews được staging rồi publish với
journal checksum để phục hồi gián đoạn. Khi đã complete, resume không nạp model
và không ghi lại các artifacts đã công bố; vẫn đọc inventory để kiểm tra input.

## Code và outputs

Code hoàn chỉnh: `pipeline/cctv/detect.py`, `scripts/download_cctv_yolo.py`.

```text
outputs/model-cache/yolo/
  yolov8m.pt
  yolo.json                      release/source URL/local path/pinned SHA
outputs/demo-data/detections/
  detections.db                  checkpoint records; không phải DB retrieval
  detections.jsonl               DetectionFrame schema v1, sorted video/time
  detection_manifest.json        recipe/count/VRAM/ROI/tracking readiness
  detect_state.json              ownership/errors/publication journal
  detect.log
  previews/N001-V001.jpg         một overlay/video gần mốc 30 giây
  ...                            tổng sáu previews với demo hiện tại
```

Overlay chỉ là JPEG mới trong output, không sửa ảnh nguồn. ROI pending vẫn cho
phép detection toàn ảnh; chưa tính occupancy hoặc traffic windows. Preview
không thay nhãn độc lập để đánh giá chất lượng.

## Chạy và kiểm thử PowerShell

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m scripts.download_cctv_yolo
# Cache đã có: chỉ xác minh, không truy cập mạng
.\.venv\Scripts\python.exe -m scripts.download_cctv_yolo --offline

# Dừng server CLIP bằng Ctrl+C trước stage detector
.\.venv\Scripts\python.exe scripts\run_python_logged.py --log outputs\demo-data-review\detection-full-console.txt -- -u -m pipeline.cctv.detect --input-dir outputs\demo-data\processed --output-dir outputs\demo-data\detections --model-config outputs\model-cache\yolo\yolo.json --traffic-config configs\cctv.traffic.demo.json --device cuda --batch-size 4
if ($LASTEXITCODE -ne 0) { throw 'Detection failed; inspect checkpoint/log.' }

# Chạy lại cùng lệnh để resume hoặc xác minh skip sau complete
.\.venv\Scripts\python.exe scripts\run_python_logged.py --log outputs\demo-data-review\detection-resume-console.txt -- -u -m pipeline.cctv.detect --input-dir outputs\demo-data\processed --output-dir outputs\demo-data\detections --model-config outputs\model-cache\yolo\yolo.json --traffic-config configs\cctv.traffic.demo.json --device cuda --batch-size 4

.\.venv\Scripts\python.exe -m unittest -v test_cctv_detect
.\scripts\Test-Windows.ps1
# Sau detector/tests, có thể chạy lại demo CLIP
.\scripts\Start-CctvDemo.ps1
```

`--stride 100` chọn frame 001/101/201/... riêng theo từng video để smoke test
(76 frame của demo); phải dùng output khác với full run. Không dùng stride này
cho tracking. `--device cpu` dùng FP32 và output mới. CLI exit 1 khi lỗi hoặc còn
frame lỗi, 130 khi Ctrl+C; logged runner giữ exit code và stderr trong log.

Tám tests persistence/resume, interruption, publication recovery, invalid record,
batch splitting, đổi recipe, output protection và corruption dùng detector tổng
hợp CPU, không tải weights và không đánh giá độ chính xác YOLO.

## Nghiệm thu thực tế T3.2

- Full run: 7.232 frames, sáu video, `done=7232, skipped=0, error=0`, batch 4.
- 328,961 giây decode/inference/commit (không gồm kiểm kê hash và publication).
  Peak PyTorch CUDA: allocated 183,82 MiB, reserved 282 MiB; không phải tổng VRAM
  Windows hoặc đo end-to-end throughput của streaming video.
- Resume: `done=0, skipped=7232, error=0, model_loaded=false`. Hash và mtime của
  chín artifacts publication, state và log đều không đổi.
- 63 core tests đạt trên Windows PowerShell 5.1, runtime CUDA FP16/NMS đạt.
  Đây không phải toàn bộ legacy suite hoặc benchmark detector.
- Detections có locator khớp đủ 7.232 rows retrieval. Inventory ảnh/map khớp
  fingerprint đã dùng tạo index; bốn artifacts retrieval giữ checksum. Sáu video
  gốc giữ SHA-256/mtime. ROI và tracking readiness vẫn false.
- Đã xem overlay ngày N001-V001/061.jpg tại 30,037 giây và đêm N002-V001/061.jpg
  tại 30,006 giây. Boxes hiện đúng tọa độ ảnh, nhưng còn nhầm lớp car/truck và bỏ
  sót xe nhỏ/xa, đặc biệt cảnh đêm. Chưa có nhãn để chấm precision/recall/mAP.

Có 150.204 boxes trên toàn bộ frames (lặp cùng xe qua nhiều frames); không dùng
con số này làm số xe duy nhất hoặc lưu lượng. Chưa đếm theo ROI, loại xe đỗ hoặc
suy ra congestion. Báo cáo cục bộ: `outputs/demo-data-review/detection-verification.json`;
logs: `detection-full-console.txt`, `detection-resume-console.txt`,
`outputs/tests/core-tests.txt`. Weights, detections và footage không commit.

## Git

```powershell
git diff --check
git add -- pipeline/cctv/detect.py scripts/download_cctv_yolo.py test_cctv_detect.py scripts/Test-Windows.ps1 docs/CCTV_DETECTION.md docs/CCTV_PROGRESS.md README_CCTV.md
git diff --cached --check
git commit -m "feat: add resumable CCTV vehicle detection baseline"
git push origin main
```
