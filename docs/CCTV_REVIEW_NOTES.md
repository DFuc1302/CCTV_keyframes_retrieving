# T8.2 — ghi chú riêng từng reviewer và kiểm tra trước adjudication

## Concept / architecture

`pipeline/cctv/review_notes.py` bổ sung bước giữa tư liệu T8.1 và annotation
T6.1. Hai slot được tạo riêng, reviewer ban đầu `null`, mọi interval pending.
Slot không chứng minh hai người độc lập. Công cụ kiểm tra khai báo, không
xác thực người review hoặc việc họ đã xem video/blind review.

Input được ràng buộc bằng SHA-256 của manifest và template pending. Camera,
video và khoảng PTS nguồn `[start_sec,end_sec)` phải khớp chính xác template.
Đây là ràng buộc với manifest, không phải kiểm tra checksum footage mới;
kiểm tra footage/preview thuộc bước chuẩn bị T8.1. Không tải model, không
ghi vào footage, không đổi schema annotation/index legacy.

Người review dùng ảnh/video sạch ở `CCTV_REVIEW_PREPARATION.md`, xem toàn bộ
khoảng và đọc ontology trong `CCTV_BENCHMARK.md`. Không được khai báo blind
nếu đã xem predictions cho cùng khoảng. Ảnh contact sheet không thay thế
việc xem video. Khoảng demo hiện có không phải blind test mới đại diện.

## Hợp đồng ghi chú

Giữ nguyên `schema_version`, `mode`, hai SHA, slot, camera/video và coverage.
Mỗi người tự ghi tên thật vào `reviewer`. Công cụ so sánh tên bằng Unicode
NFKC/casefold để chặn một số alias đơn giản; đây không phải xác minh danh tính.

| Trường / trạng thái | Quy tắc |
| --- | --- |
| `pending` | Chưa quan sát; timestamp null, flags false, reason rỗng, events rỗng |
| `unknown` | Chưa đủ ngữ cảnh; tên reviewer, timestamp có timezone và reason bắt buộc; không gán events |
| `complete` | Tên reviewer, timestamp có timezone, reason; `full_interval_watched` và `ontology_reviewed` true |
| `blind_to_predictions` | Khai báo trung thực; false làm comparison không đủ điều kiện blind |
| `events` | Chỉ complete; mỗi event có ID riêng, label và start/end nằm trong coverage |

Labels hiện có: `congestion`, `road_incident`, `accident`, `moving_to_stop`.
ID sự kiện duy nhất trong file; cùng sự kiện ở hai reviewer có thể có ID khác.
Không coi move-then-stop là tai nạn nếu bằng chứng chưa đủ. `complete` với
events rỗng là quan sát âm tính của một người; pending/unknown không là âm tính.
Timestamp ghi nhận dùng ISO 8601 có timezone, ví dụ `2026-10-01T10:00:00+07:00`;
không suy timestamp ghi hình tuyệt đối từ tên video.

Comparison đếm năm nhóm: pending, unknown, không blind, đồng thuận và bất đồng.
Đồng thuận là multiset `(label,start_sec,end_sec)` bằng nhau chính xác; ID/reason
không tham gia và không có tolerance. Nhãn trùng span vẫn giữ số lần xuất hiện.
Đây là kiểm tra trước adjudication, không là chỉ số chất lượng hay độ tin cậy
reviewer. Cả agreement và disagreement đều cần quyết định adjudication của
con người; không auto-merge, không xuất annotations reviewed hoặc chấm metrics.

## Code / PowerShell chạy và kiểm thử

Module đầy đủ: `pipeline/cctv/review_notes.py`; kiểm thử fixtures:
`test_cctv_review_notes.py`. Dùng Python trong venv trên Windows:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
& .\.venv\Scripts\python.exe -m pipeline.cctv.review_notes init `
  --manifest configs/cctv.demo.json `
  --annotations configs/cctv.annotations.pending.json `
  --output outputs/review-notes-templates-v1
```

Templates được sealed để resume. Người review chỉnh bản copy trong hai thư
mục working riêng, không chỉnh templates/state hoặc comparison report. Lệnh
sau chỉ copy biểu mẫu pending; không thực hiện hoặc giả lập human review:

```powershell
New-Item -ItemType Directory -Path outputs/reviewer-a-working, outputs/reviewer-b-working -Force | Out-Null
Copy-Item -LiteralPath outputs/review-notes-templates-v1/reviewer-a.pending.json `
  -Destination outputs/reviewer-a-working/notes.json
Copy-Item -LiteralPath outputs/review-notes-templates-v1/reviewer-b.pending.json `
  -Destination outputs/reviewer-b-working/notes.json
```

Chỉ chạy copy một lần khi working file chưa tồn tại để tránh ghi đè ghi chú.
Sau khi mỗi người tự ghi ghi chú, kiểm tra bằng lệnh:

```powershell
& .\.venv\Scripts\python.exe scripts/run_python_logged.py `
  --log outputs/tests/reviewer-check.txt -- `
  -m pipeline.cctv.review_notes check `
  --manifest configs/cctv.demo.json `
  --annotations configs/cctv.annotations.pending.json `
  --review-a outputs/reviewer-a-working/notes.json `
  --review-b outputs/reviewer-b-working/notes.json `
  --output outputs/reviewer-comparison-v1
Get-Content outputs/tests/reviewer-check.txt
Get-Content outputs/reviewer-comparison-v1/comparison_report.json
.\scripts\Test-Windows.ps1
```

Thay đổi notes/template/code cần **output mới**, ví dụ comparison-v2; giữ
report cũ để audit. Resume với inputs không đổi giữ SHA/mtime artifacts.
File output chỉnh/sai/mất được báo lỗi và giữ lại; không overwrite. Input
JSON giới hạn 64 MiB, 10.000 interval/file, 100 events/interval. `tqdm` báo
tiến độ theo interval; dữ liệu không hợp lệ dừng trước xuất report để tránh
một report partial bị hiểu là kiểm tra thành công.

## Kết quả demo và Git

Hai templates và báo cáo pending local ở `outputs/review-notes-templates-v1`
và `outputs/review-notes-pending-check-v1`. Sáu interval pending, reviewer
null; `ground_truth_published=false`, `reviewed_intervals=0`, operational false.
11 unit tests sử dụng reviewer/events giả trong thư mục tạm. Không đưa
ghi chú cá nhân, dữ liệu hoặc outputs lên GitHub.

```powershell
git add pipeline/cctv/review_notes.py test_cctv_review_notes.py scripts/Test-Windows.ps1 docs/CCTV_REVIEW_NOTES.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md
git commit -m "feat: add separate reviewer notes and pre-adjudication validation"
git push origin main
```

Bước tiếp theo T8.3: công cụ ghi quyết định adjudication rõ ràng và chuyển
sang schema annotation T6.1, có kiểm thử bằng fixtures. Nhãn thật vẫn cần
hai người review và quyết định thực; ROI/grouping, máy thứ hai còn mở.
