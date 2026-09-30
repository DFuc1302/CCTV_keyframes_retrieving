# T4.2 — Temporal incident screening baseline

## Concept và rationale

Đây là baseline **sàng lọc xe di chuyển rồi dừng kéo dài**, không phải mô hình
nhận diện va chạm/tai nạn. Rule có thể bắt xe chờ đèn đỏ hoặc trong hàng xe.
Fixtures cố tình bao gồm chờ đèn đỏ để giữ giới hạn này nhìn thấy được.
Không dùng CLIP score làm xác suất tai nạn và không tự xác nhận event.

Tính chuyển động tâm bbox trong pixel chia trung bình đường chéo bbox của hai
frame rồi chia delta PTS. Đơn vị là **bbox diagonals/giây** (tên trường config
`body_lengths_per_sec` là đại lượng xấp xỉ trong ảnh), không phải m/s hoặc
km/h. Chuẩn hóa này giảm phụ thuộc kích thước xe trong ảnh; vẫn chịu ảnh
hưởng phối cảnh, bbox jitter, camera rung và lỗi association.

Config demo chọn moving ≥0.3, stationary ≤0.04, moving ≥1 giây rồi stationary
≥2 giây. Khoảng giữa hai ngưỡng phá streak; baseline chỉ tìm chuyển trạng thái
rõ ràng, có thể bỏ sót xe giảm tốc từ từ. Các giá trị là lựa chọn kỹ thuật
để thử pipeline, **chưa được hiệu chỉnh hoặc chứng minh bằng nhãn thực tế**.
Confidence ≥0.4, bbox area ≥0.0002 và scale ratio ≤1.5 lọc bằng chứng yếu.
Bottom-center bbox phải nằm trong ROI. Dừng ngay từ đầu clip không đủ bằng
chứng về chuyển trạng thái.

## Kiến trúc và code hoàn chỉnh

- `pipeline/cctv/incidents.py`: config, temporal state machine, schema v2,
  read-only tracking input, checkpoint từng video, publication và CLI.
- `pipeline/cctv/incident_fixtures.py`: 10 quỹ đạo tổng hợp và report logic.
- `configs/cctv.incidents.demo.json`: recipe chưa hiệu chỉnh.
- `test_cctv_incidents.py`: 12 tests CPU, bao gồm fixtures, provenance/resume,
  lỗi frame, output safety và review v2.
- `pipeline/cctv/event_review.py`: đọc được package T4.1 v1 và incident v2.

State theo `(run,camera,video,segment,track_id)`; không nối track ID qua
segment. Mất observation, confidence thấp, ra khỏi ROI, bbox đổi scale quá
lớn, frame ordinal gián đoạn hoặc PTS gap >0.5 giây reset bằng chứng. Không
dùng FPS giả để thay PTS. Mỗi stop liên tục chỉ tạo một ứng viên sau khi đạt
persistence. Khi xe đi lại, có thể bắt một episode mới.

Schema v2 giữ locator/evidence/flags/review fields của v1, thêm `rule`,
`segment_id`, `track_id`, `moving_sec`, `stationary_sec`; event type là
`suspected_road_incident`. Evidence đầu/cuối là frame thật của cùng segment.
`end_sec` là timestamp xác nhận cộng một tick timebase để khoảng `[start,end)`
chứa frame cuối; không phải thời điểm kết thúc sự cố ngoài thực tế.

V1 package và journal vẫn đọc/review được, không sửa bytes hoặc IDs cũ.
V2 tạo output riêng. ROI pending yêu cầu `--allow-draft`; output giữ
`demo_only=true`, `thresholds_calibrated=false`, `operational_ready=false`.
Không tự review footage hoặc phê duyệt ROI.

Pipeline chạy CPU, streaming SQLite `mode=ro`, không tải thêm model/GPU.
Có tqdm, log lỗi từng frame; frame hỏng làm video fail và không xuất package
complete, tránh suy diễn continuity qua dữ liệu lỗi. Resume kiểm tra checksum
và skip checkpoint; input/recipe thay đổi phải dùng output mới. Raw và mọi
upstream artifacts chỉ đọc; không tạo lại detections, tracks hoặc FAISS.

## PowerShell run/test

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'

& $python -m pipeline.cctv.incident_fixtures `
  --manifest configs\cctv.demo.json `
  --output outputs\demo-data\incident-fixtures-v1

& $python -m pipeline.cctv.incidents `
  --manifest configs\cctv.demo.json --roi configs\cctv.roi-draft.json `
  --config configs\cctv.incidents.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\incidents-draft-v1 --allow-draft

# Chạy lại đúng command trên: resumed=true và videos_skipped=6.
& $python -m pipeline.cctv.event_review list `
  --events-dir outputs\demo-data\incidents-draft-v1 `
  --review-dir outputs\demo-data\incident-reviews-v1

& $python scripts\run_python_logged.py --log outputs\tests\incident-tests.txt -- `
  -m unittest -v test_cctv_incidents
.\scripts\Test-Windows.ps1
```

Nếu có ứng viên, dùng `event_review review` theo hướng dẫn T4.1, nhập ID thực
từ lệnh list, người review/lý do và revision hiện tại. Confirm chỉ là quyết
định review; các flags draft/uncalibrated và operational_ready không thay đổi.
Chưa thêm incident queue/overlay vào UI.

## Verification và giới hạn

Chạy sáu clip `[30,40)` từ 1.501 tracked frames: **0 ứng viên** theo recipe
này. Đây không phải kết luận sáu video không có sự cố; chỉ 60 giây tổng đã
được sàng lọc, segment có reset và ngưỡng chưa hiệu chỉnh.

10 fixtures đạt kỳ vọng logic: move-then-stop và red-light-stop đều tạo 1
ứng viên; 8 tình huống không đạt điều kiện tạo 0. “Đúng kỳ vọng rule” không
phải độ chính xác nhận diện sự cố. Chưa đo precision/recall trên CCTV có nhãn.
Report local: `outputs/demo-data/incident-fixtures-v1/fixture-report.json` và
`outputs/demo-data-review/incident-verification.json`. Không push dữ liệu CCTV.

Kế hoạch đánh giá: [`CCTV_EVENT_EVALUATION.md`](CCTV_EVENT_EVALUATION.md).

## Git

```powershell
git add pipeline/cctv/incidents.py pipeline/cctv/incident_fixtures.py `
  pipeline/cctv/event_review.py configs/cctv.incidents.demo.json `
  test_cctv_incidents.py scripts/Test-Windows.ps1 docs/CCTV_INCIDENTS.md `
  docs/CCTV_EVENT_EVALUATION.md docs/CCTV_EVENTS.md docs/CCTV_PROGRESS.md README_CCTV.md README.md
git commit -m 'feat: screen scoped CCTV tracks for temporal incident candidates'
git push origin main
```
