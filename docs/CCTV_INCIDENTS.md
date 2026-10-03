# T4.2 — Temporal incident screening baseline

## Concept và rationale

Đây là baseline **sàng lọc xe di chuyển rồi dừng kéo dài**, không phải mô hình
nhận diện va chạm/tai nạn. Bộ lọc ngữ cảnh nhiều xe giảm ứng viên do dừng
theo hàng xe hoặc tín hiệu đèn, nhưng không xác định nguyên nhân dừng.
Fixture `red_light_stop_confounder` vẫn là **một xe chờ đèn đỏ tạo 1 ứng viên**:
không có đủ xe lân cận để phân biệt với sự cố. Đây là giới hạn chưa giải quyết,
không phải tình huống đã nhận diện đúng. Không dùng CLIP score làm xác suất
tai nạn và không tự xác nhận event.

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

### Bộ lọc ngữ cảnh hàng xe

`configs/cctv.incidents.demo.json` bật bộ lọc theo mặc định:

| Trường | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `queue_filter_enabled` | `true` | Bật kiểm tra hàng xe; `false` khôi phục rule từng xe |
| `neighbor_radius_diagonals` | `3.0` | Bán kính dương, tính bằng khoảng cách bottom-center chia trung bình đường chéo hai bbox |
| `min_queue_neighbors` | `2` | Số xe lân cận tối thiểu, số nguyên từ 1 đến 100; mặc định cần ít nhất 3 xe kể cả xe đang xét |
| `stop_sync_window_sec` | `2.0` | Cửa sổ dương cho chênh lệch thời điểm bắt đầu dừng |

Mỗi frame xử lý hai lượt: cập nhật chuyển động của tất cả observation hợp lệ
rồi xét ngữ cảnh. Thứ tự observation trong frame không thay đổi quyết định.
Khoảng cách dùng bottom-center trong pixel của frame nguồn và trung bình
đường chéo bbox, không phải khoảng cách trên mặt đường. Xe lân cận phải có
hướng di chuyển trước khi dừng tương thích (cosine ≥0.5, lệch tối đa 60°), và:

- bắt đầu dừng trong cửa sổ đồng bộ; hoặc
- đã đứng yên ít nhất 1 giây, để hỗ trợ xe mới nhập vào hàng đang dừng.

Nếu đủ xe hỗ trợ, chuyển trạng thái được coi là `queue_or_signal_stop` nội bộ
và **không xuất event**. Xe xa, đi ngược hướng hoặc đứng yên từ đầu mà không
có hướng di chuyển trước đó không đủ chứng cứ hàng xe.

Sau khi bị lọc, xe không trở thành ứng viên chỉ vì số xe lân cận giảm xuống
dưới ngưỡng. Miễn còn một xe hỗ trợ trong bán kính, kể cả xe đó đã bắt đầu
di chuyển, ứng viên vẫn bị giữ lại. Khi **tất cả** xe hỗ trợ không còn gần
liên tục **hơn 5 giây**, xe vẫn dừng mới tạo một event
`suspected_road_incident`. Đúng 5 giây chưa tạo event; xe hỗ trợ quay lại
hoặc ngữ cảnh hàng xe mới xuất hiện làm reset bộ đếm. Mỗi stop chỉ phát một
event. Reset do mất/weak observation, gap hoặc đổi segment vẫn xóa bằng chứng.

## Kiến trúc và code hoàn chỉnh

- `pipeline/cctv/incidents.py`: config, temporal state machine hai lượt với
  ngữ cảnh hàng xe, schema v2, read-only tracking input, checkpoint từng video,
  publication và CLI.
- `pipeline/cctv/incident_fixtures.py`: quỹ đạo tổng hợp và report kỳ vọng logic,
  gồm hàng xe đồng bộ, hàng xe có sẵn, hướng ngược và rời hàng xe.
- `configs/cctv.incidents.demo.json`: recipe chưa hiệu chỉnh.
- `test_cctv_incidents.py`: tests CPU cho temporal/queue behavior, thứ tự
  observation, provenance/resume, lỗi frame, output safety và review v2.
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
  --output outputs\demo-data\incident-fixtures-queue-v1

& $python -m pipeline.cctv.incidents `
  --manifest configs\cctv.demo.json --roi configs\cctv.roi-draft.json `
  --config configs\cctv.incidents.demo.json `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\incidents-queue-draft-v1 --allow-draft

# Chạy lại đúng command trên với input/recipe không đổi để resume.
& $python -m pipeline.cctv.event_review list `
  --events-dir outputs\demo-data\incidents-queue-draft-v1 `
  --review-dir outputs\demo-data\incident-queue-reviews-v1

& $python scripts\run_python_logged.py --log outputs\tests\incident-tests.txt -- `
  -m unittest -v test_cctv_incidents
.\scripts\Test-Windows.ps1
```

Nếu có ứng viên, dùng `event_review review` theo hướng dẫn T4.1, nhập ID thực
từ lệnh list, người review/lý do và revision hiện tại. Confirm chỉ là quyết
định review; các flags draft/uncalibrated và operational_ready không thay đổi.
Chưa thêm incident queue/overlay vào UI.

## Verification và giới hạn

Kết quả lịch sử của recipe trước bộ lọc hàng xe: sáu clip `[30,40)` từ 1.501
tracked frames tạo **0 ứng viên**. Không dùng kết quả này làm bằng chứng đã
chạy recipe mới, hoặc kết luận sáu video không có sự cố; chỉ 60 giây tổng đã
được sàng lọc, segment có reset và ngưỡng chưa hiệu chỉnh. Báo cáo cũ
`outputs/demo-data/incident-fixtures-v1/fixture-report.json` và
`outputs/demo-data-review/incident-verification.json` giữ nguyên, không sửa
artifact đã sealed để cập nhật kết luận.

Các fixture mới quy định kỳ vọng: ba xe gần dừng cùng hướng tạo 0 ứng viên;
xe đơn di chuyển rồi dừng vẫn tạo 1; xe bị giữ lại chỉ tạo 1 sau hơn 5 giây
kể từ lúc xe hỗ trợ cuối cùng rời bán kính. Fixture còn kiểm tra một xe hỗ trợ
vẫn giữ suppression, hàng xe có sẵn cùng/ngược hướng, xe xa, thiếu hàng xóm,
tắt bộ lọc và thay thứ tự observation. Đây là **kỳ vọng logic tổng hợp**,
không phải số đo precision/recall hoặc kết quả chạy mới. Dùng lệnh ở trên
để tạo report riêng khi xác minh recipe mới.

“Đúng kỳ vọng rule” không phải độ chính xác nhận diện sự cố. Một xe chờ đèn
đỏ vẫn là confounder; sự cố nằm trong hàng xe có thể bị giữ lại đến khi hàng
xe rời đi, hoặc bị giữ vô thời hạn nếu còn xe hỗ trợ gần đó. Occlusion,
association sai, phối cảnh và bbox jitter có thể làm sai hướng hoặc suy diễn
xe đã rời hàng. Không có ground truth về trạng thái đèn hay va chạm.
Chưa đo precision/recall trên CCTV có nhãn; vẫn cần người thật review footage,
phê duyệt ROI và hiệu chỉnh ngưỡng. Mọi ứng viên vẫn `pending_review`,
`thresholds_calibrated=false`, `operational_ready=false`; fixture tổng hợp
vẫn `synthetic_event=true`, `demo_only=true`. Không push dữ liệu CCTV.

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
