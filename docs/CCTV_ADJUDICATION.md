# T8.3 — quyết định adjudication và xuất annotation

## Concept / architecture rationale

`pipeline/cctv/adjudication.py` đọc hai notes T8.2 trực tiếp, tính lại comparison,
không tin một report do người dùng sửa. Quyết định được ràng buộc bằng SHA-256
manifest, pending template và hai notes theo đúng thứ tự A/B. Init luôn tạo
pending, kể cả khi hai notes complete và đồng thuận. Chỉ quyết định tường minh
mới xuất trạng thái reviewed; không gộp events tự động.

Output giữ schema `AnnotationSet` T6.1: camera/video, khoảng PTS nửa mở,
review status, reviewer names, timestamp, blind flag và events. Reason,
adjudicator, acknowledgment và provenance đầy đủ lưu riêng trong report.
Unknown/pending không thành negative coverage. Explicit reviewed với events
rỗng là kết luận âm tính do người review cung cấp.

Module chỉ xử lý JSON trên CPU, không tải model hoặc ghi footage/index.
Không thay grouping/split hoặc phê duyệt ROI. Hash binding xác nhận input
manifest/notes, không xác thực con người, hành vi blind review hay checksum
footage mới; kiểm tra footage nằm ở T8.1.

## Hợp đồng quyết định

| Trường | Yêu cầu |
| --- | --- |
| `adjudicator` | Tên người ghi quyết định thực; init null, nonpending phải có tên |
| `status=pending` | Timestamp null, reason/events rỗng, hai flags false |
| `status=unknown` | Timestamp có timezone và reason; không events hoặc final acceptance |
| `status=reviewed` | Hai notes complete/blind của hai reviewer khác nhau; reason và timestamp hợp lệ |
| `reviewers_accepted_final` | Reviewed cần true, chỉ khai báo khi cả hai người đã chấp nhận kết quả cuối |
| `blind_to_predictions` | Reviewed cần true cho adjudication; không khai báo nếu đã xem predictions |
| `decided_at` | ISO 8601 có timezone, không trước timestamp notes đã ghi |
| `events` | Chỉ reviewed, bốn labels T6.1, ID duy nhất toàn bộ export, start/end trong coverage |

Giữ nguyên camera/video/coverage và tất cả SHA. Không đoán reason, reviewer
names hoặc acknowledgment. Khi chưa đủ context, dùng unknown; khi chưa xử lý,
giữ pending. Nếu chưa có hai notes đủ điều kiện, reviewed export bị từ chối.
Hai người có thể bất đồng lúc đầu; sau thảo luận, kết luận cuối có thể khác
cả hai proposals, nhưng phải có reason và khai báo final acceptance thực.
Tools kiểm tra khai báo, không chứng minh các khai báo là đúng.

## Complete executable code / PowerShell run and test

Code đầy đủ: `pipeline/cctv/adjudication.py`; fixtures và regression gates:
`test_cctv_adjudication.py`. Sau khi hai reviewer thực ghi notes riêng theo
`CCTV_REVIEW_NOTES.md`, tạo template mới gắn với đúng phiên notes:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
& .\.venv\Scripts\python.exe scripts/run_python_logged.py `
  --log outputs/tests/adjudication-init.txt -- `
  -m pipeline.cctv.adjudication init `
  --manifest configs/cctv.demo.json `
  --annotations configs/cctv.annotations.pending.json `
  --review-a outputs/reviewer-a-working/notes.json `
  --review-b outputs/reviewer-b-working/notes.json `
  --output outputs/adjudication-working-template-v1
```

Copy template thành working file để con người chỉnh, không chỉnh sealed output:

```powershell
$decisionWorking = 'outputs/adjudicator-working/decisions.json'
if (Test-Path -LiteralPath $decisionWorking) { throw 'Working decisions already exist; preserve them.' }
New-Item -ItemType Directory -Path outputs/adjudicator-working -Force | Out-Null
Copy-Item -LiteralPath outputs/adjudication-working-template-v1/adjudication.pending.json `
  -Destination $decisionWorking
```

Sau khi có quyết định thực, xuất sang thư mục mới:

```powershell
& .\.venv\Scripts\python.exe scripts/run_python_logged.py `
  --log outputs/tests/adjudication-export.txt -- `
  -m pipeline.cctv.adjudication export `
  --manifest configs/cctv.demo.json `
  --annotations configs/cctv.annotations.pending.json `
  --review-a outputs/reviewer-a-working/notes.json `
  --review-b outputs/reviewer-b-working/notes.json `
  --decisions outputs/adjudicator-working/decisions.json `
  --output outputs/adjudicated-annotations-v1
Get-Content outputs/tests/adjudication-export.txt
Get-Content outputs/adjudicated-annotations-v1/adjudication_report.json
.\scripts\Test-Windows.ps1
```

`--synthetic-fixture` chỉ dành cho ghi chú/quyết định tổng hợp: export mode
synthetic, không là nhãn độc lập footage thật. Fixtures unit tests luôn dùng
cờ này. Không dùng tên hoặc timestamp giả cho các mẫu demo thật.

JSON giới hạn 64 MiB/input, tối đa 10.000 interval và 100 events/interval.
`tqdm` báo tiến độ; lỗi schema/provenance/coverage chặn toàn bộ export trước
publication, không skip lỗi rồi tạo tập nhãn partial. Artifact/checkpoint
immutable, inputs và recipe SHA đóng băng; resume không ghi lại artifacts.
Nếu notes/decision/code đổi, chọn output mới. Outputs sửa/mất bị báo lỗi và
bảo toàn; interruption trước checkpoint cần output mới để giữ phần đã ghi.

## Kết quả kiểm thử và Git

Demo local dùng nguyên notes pending T8.2: template tại
`outputs/adjudication-template-v1`, export tại
`outputs/adjudication-pending-export-v1`. Sáu khoảng vẫn pending, 0 reviewed,
`independent_ground_truth_exported=false`. Đây là kiểm thử luồng dữ liệu,
không phải human adjudication. Evaluator T6.1 nhận annotations này nhưng
không tính metrics nếu chưa có reviewed coverage/split hợp lệ.

10 tests mới dùng fixtures kiểm tra explicit positive/negative, pending,
unknown, acceptance/blind/identity/time/bounds, SHA notes và output protection.
Không commit dữ liệu, personal notes hoặc outputs:

```powershell
git add pipeline/cctv/adjudication.py test_cctv_adjudication.py scripts/Test-Windows.ps1 docs/CCTV_ADJUDICATION.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md
git commit -m "feat: add explicit adjudication and legacy annotation export"
git push origin main
```

T8.1–T8.3 đã có công cụ. Nghiệm thu chất lượng tiếp theo cần review thực,
adjudication, xác nhận recording groups/splits và ROI; máy thứ hai vẫn cần
chạy thực theo checklist bàn giao. Các tools không thay bước xác minh này.
