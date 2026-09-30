# T5.2 — Simulation allocation proposals

## Concept / architecture rationale

T5.2 tạo **phương án mô phỏng**, không gửi lệnh điều động. Zone, team, travel
times và demands mẫu đều giả định; camera locations/nguồn lực thật vẫn chưa
xác nhận. `dry_run=true`, `fictional_resource_data=true` và
`operational_ready=false` luôn được giữ, kể cả khi event đã confirmed.

Mỗi demand có min_officers, required_skills, priority 1–5 và max_travel_sec.
Những giá trị này do kịch bản/người dùng quy định; không tự suy mức nghiêm
trọng tai nạn hoặc quân số cần thiết từ CLIP/traffic rule.

Hai nguồn demand riêng:

- `synthetic_fixture`: camera/review_status giả định ghi trong config, toàn
  bộ demand có `synthetic_demand=true`. “confirmed” ở đây là trạng thái bài
  tập, không phải review footage.
- `event_package`: config chỉ có event_id và staffing requirements. Camera,
  review_status, synthetic/demo flags được đọc từ sealed event v1/v2 và
  journal T4.1. Không nhận review_status từ request. Journal chưa tồn tại
  nghĩa là pending; thao tác đọc không tạo journal. Event không thuộc
  manifest/package hoặc bị request hai lần bị từ chối.

## Model / solver

Whole teams không chia nhỏ; mỗi team chỉ dùng cho tối đa một demand trong
snapshot. Demand được đáp ứng trọn min_officers hoặc nằm trong unmet list;
không gán một phần rồi báo đã đáp ứng. Mỗi team của demand phải có đủ tất
cả required_skills, available, được phép đến zone, có direct travel cost
và đáp ứng travel deadline. Đây là mô hình kỹ năng bảo thủ; chưa ghép kỹ
năng bổ sung giữa những team khác nhau.

Tổng quân số được gán không vượt cap T5.1:
`min(max_dispatch_officers, max(0, available_officers - reserve_officers))`.
Busy/offline không làm dự phòng. Nếu nguồn available vốn ít hơn reserve,
không gán người và `reserve_met=false`; không che thiếu hụt này.

Mục tiêu lexicographic:

1. Tối đa số demand đáp ứng ở priority 5, rồi 4, 3, 2, 1.
2. Cùng mức đáp ứng, tối thiểu tổng travel cost của các team được dùng.
3. Sau đó tối thiểu quân số; cuối cùng tie-break mask xác định.

Một demand priority cao được ưu tiên hơn nhiều demand thấp; đây là policy
của bài tập, không phải chính sách cảnh sát đã được phê duyệt. Travel cost
là tổng thời gian giả định của team, không phải thời gian đến cuối cùng hoặc
ETA thật. Cùng zone=0 là giả thiết mô hình; chưa có mobilization delay.

DP lưu phương án tốt nhất theo used-team bitmask sau mỗi demand. Hai phương
án dùng cùng tập team có cùng nguồn lực còn lại; giữ counts/travel tốt hơn
không làm mất phương án tối ưu cho demand tiếp theo. Loại supersets thừa
quân số vì travel không âm và objective sau cùng giảm quân số.

Bộ giải chính xác trong giới hạn **12 team, 12 demand, 2.000.000 bước tìm
kiếm**. Vượt giới hạn báo lỗi, không xuất partial proposal hay tự đổi sang
heuristic. Không phù hợp mạng lưới điều phối lớn; mở rộng sau bằng solver
tối ưu riêng và kiểm chứng objective/constraints tương đương.

## Complete executable code

- `pipeline/cctv/dispatch.py`: demand contracts, source/review binding, solver,
  assignment/unmet reasons, SHA-bound report, atomic state và resume.
- `configs/cctv.demands.simulation.json`: 3 nhu cầu giả định.
- `configs/cctv.demands.events.json`: event request template rỗng.
- `test_cctv_dispatch.py`: 14 tests và oracle brute-force độc lập cho kịch
  bản nhỏ; kiểm tra skill trap, toàn bộ cap/reserve, review và publication.

Input manifest/resource/demand/event/journal chỉ đọc. Fingerprints kiểm tra
trước/sau solve; thay đổi trong lúc đọc/tính toán chặn publication. Resume
kiểm tra input/recipe/review và skip solver, giữ nguyên report/state. Journal
hoặc config thay đổi yêu cầu output mới. Report là snapshot có provenance,
không tự cập nhật hoặc tự áp dụng khi tình hình mới xuất hiện.

## PowerShell run/test

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'

& $python -m pipeline.cctv.dispatch `
  --manifest configs\cctv.demo.json `
  --resources configs\cctv.resources.simulation.json `
  --demands configs\cctv.demands.simulation.json `
  --output outputs\demo-data\dispatch-simulation-v1

# Chạy lại nguyên command để kiểm tra resumed=true và không chạy solver.
Get-Content outputs\demo-data\dispatch-simulation-v1\dispatch_report.json

# Event package hiện có rỗng: không tự bịa demand từ footage.
& $python -m pipeline.cctv.dispatch `
  --manifest configs\cctv.demo.json `
  --resources configs\cctv.resources.simulation.json `
  --demands configs\cctv.demands.events.json `
  --events-dir outputs\demo-data\incidents-draft-v1 `
  --review-dir outputs\demo-data\incident-reviews-v1 `
  --output outputs\demo-data\dispatch-events-empty-v1

# Thử gate trên case demo tổng hợp T4.1, không sửa review journal.
$demoEvent = Get-Content outputs\demo-data\events-demo-v1\events.jsonl | ConvertFrom-Json
$request = @{
  schema_version = 1
  mode = 'simulation'
  kind = 'event_package'
  demands = @(@{
    demand_id = 'SIM-EVENT-REVIEW'
    event_id = $demoEvent.event_id
    min_officers = 2
    required_skills = @('scene_assessment')
    priority = 4
    max_travel_sec = 180.0
  })
}
$request | ConvertTo-Json -Depth 8 | Set-Content `
  outputs\demo-data-review\dispatch-event-request.json -Encoding UTF8

& $python -m pipeline.cctv.dispatch `
  --manifest configs\cctv.demo.json `
  --resources configs\cctv.resources.simulation.json `
  --demands outputs\demo-data-review\dispatch-event-request.json `
  --events-dir outputs\demo-data\events-demo-v1 `
  --review-dir outputs\demo-data\events-demo-reviews-v1 `
  --output outputs\demo-data\dispatch-review-gate-v1

& $python scripts\run_python_logged.py --log outputs\tests\dispatch-tests.txt -- `
  -m unittest -v test_cctv_dispatch
.\scripts\Test-Windows.ps1
```

Kịch bản mặc định: `SIM-TEAM-A` (2 người) → `SIM-TRAFFIC-A` priority 4;
giữ 2 người dự phòng. `SIM-SCENE-B` priority 3 unmet do shared cap/competing
demand. `SIM-PENDING-B` priority 5 unmet vì chưa confirmed. Ưu tiên cao không
bỏ qua review gate. Không coi staffing requirements này là dữ liệu sự cố thật.

Empty event requests/package → 0 assignments/0 unmet. Case tổng hợp T4.1
đang needs_more_evidence ở revision 3 → 0 assignments, review_not_confirmed.
Tests sử dụng journal fixtures riêng để xác minh confirmed → assignment và
giữ synthetic/demo flags; không xác nhận footage ngoài tests.

Unmet report giữ reasons và từng team bị chặn bởi availability/skill/zone/
travel. Lý do `shared_staff_cap_or_competing_demand` chỉ ra giới hạn chung,
không tuyên bố mọi team đều bận. Ngưỡng/kỹ năng/priority và allocation phải
được con người đánh giá lại trước bất kỳ ứng dụng thực tế nào.

Report local: `outputs/demo-data-review/dispatch-verification.json`. Raw,
index, tracking, event/review và resource publications giữ nguyên. Chưa có
dispatch queue trên UI; demo retrieval/traffic cổng 8080 tiếp tục hoạt động.

## Git

```powershell
git add pipeline/cctv/dispatch.py configs/cctv.demands.simulation.json `
  configs/cctv.demands.events.json test_cctv_dispatch.py scripts/Test-Windows.ps1 `
  docs/CCTV_DISPATCH.md docs/CCTV_PROGRESS.md README_CCTV.md README.md
git commit -m 'feat: propose constrained simulated patrol allocations with unmet reasons'
git push origin main
```

Tiếp theo T6.1: annotation validator, split manifest và evaluator executable.
Chưa có nhãn độc lập/ROI calibration; benchmark thật còn mở. T5.2 chỉ là
baseline mô phỏng có kiểm thử, chưa nghiệm thu chất lượng điều phối thực tế.
