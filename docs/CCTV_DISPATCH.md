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
3. Sau đó tối thiểu quân số; cuối cùng tối thiểu tuple mask theo demand đã
   sắp `(-priority, demand_id)`, với bit team theo `unit_id` tăng dần.

Một demand priority cao được ưu tiên hơn nhiều demand thấp; đây là policy
của bài tập, không phải chính sách cảnh sát đã được phê duyệt. Travel cost
là tổng thời gian giả định của team, không phải thời gian đến cuối cùng hoặc
ETA thật. Cùng zone=0 là giả thiết mô hình; chưa có mobilization delay.

Backend chính là **sparse binary MILP** qua `scipy.optimize.milp` (HiGHS),
giới hạn **100 team, 100 demand**. Chỉ tạo biến `x[u,d]` cho cặp đủ điều
kiện và biến `y[d]` báo demand fulfilled:

- `sum_d x[u,d] <= 1`: không dùng team hai lần.
- `x[u,d] <= y[d]`: không điều team tới demand chưa được đáp ứng.
- `sum_u officers[u] * x[u,d] >= min_officers[d] * y[d]`.
- Tổng officers của mọi assignment không vượt shared cap/reserve.

Các priority được tối ưu tuần tự 5 → 1 rồi khóa count bằng equality; tiếp
theo travel, officers, rồi tuple mask. Mask dùng các chunk integer có độ
lớn giới hạn và xử lý bit cao trước; không đưa mask 100-bit trực tiếp vào
float objective (mất phân biệt sau 53-bit). Mỗi stage khóa objective tối ưu
trước khi chạy stage sau, nên giảm officers/tie-break không được tăng travel.

**Fail closed:** mỗi lần gọi solver phải có status optimal, success và
`mip_gap=0` hữu hạn. Kết quả còn được kiểm tra độc lập: vector hữu hạn,
binary, eligibility, whole teams, minimum staffing, single assignment, cap
và các objective equality đã khóa. Không nhận incumbent time-limit,
infeasible/unbounded/error, positive/NaN gap hoặc nghiệm vi phạm constraints.
Lỗi chặn publication; không xuất partial proposal và không đổi sang heuristic.
`exact=true` của MILP nghĩa là đã qua certificate/validation này, không phải
bằng chứng toán học độc lập của thuật toán floating-point. Snapshot không có
biến khả thi hoặc cap=0 có certificate feasibility trực tiếp
`solver_status=trivial_optimal`, không cần gọi optimizer.

Travel được chuyển từ `Decimal(str(seconds))` sang integer ticks, chia GCD
để giữ so sánh decimal đầu vào, không làm tròn để khớp tolerance. Guard giới
hạn tổng objective travel integer khả thi tối đa ở `2**40`; equality được
khóa theo tick integer, không dùng `isclose` để cho phép travel đắt hơn.
SciPy/HiGHS vẫn dùng floating-point và tolerance: guard số học, zero-gap,
kiểm tra objective/dual bound và feasibility giảm rủi ro, nhưng không biến
certificate số thành formal exact-arithmetic proof. Input đã là float nên
không khôi phục precision bị mất trước khi nhận input.

Private oracle `_allocate_legacy` giữ DP cũ cho tối đa 12 team **và** 12
demand. Nếu thiếu SciPy hoặc travel scale vượt guard, scenario nhỏ này
có fallback DP kèm `RuntimeWarning` rõ ràng; DP vẫn dùng travel float cũ
và giới hạn 2.000.000 bước. Scenario lớn hơn báo lỗi thay vì làm tròn,
heuristic hay fallback không có cảnh báo. Cài môi trường theo lock của
project để có SciPy; report ghi phiên bản backend thực tế, không giả định
phiên bản từ tài liệu.

Report giữ shape assignment/unmet và flags simulation. Metadata gồm
`solver_backend`, `solver_version`, `solver_status`, `solver_stages`,
`solver_nodes`. Với MILP, `search_steps=solver_nodes` là tổng số node
branch-and-bound của mọi stage (có thể bằng 0 khi presolve giải xong),
không còn là số trạng thái DP hoặc runtime estimate. Fallback DP vẫn
dùng số transition của DP làm `search_steps`.

Runtime <1 giây cho fixture 25×25 là **mục tiêu benchmark**, không phải
cam kết cho mọi hardware/scenario. Competing eligibility, travel precision,
staff cap và nhiều tie-break stage có thể làm solve lâu hơn. Regression
không đặt wall-time assertion dễ hỏng theo máy; smoke benchmark đo runtime
riêng và báo môi trường cùng certified result.

Smoke local sau cutover: 25 team/25 demand cạnh tranh kỹ năng, 25 assignments,
0 unmet, status optimal, 29 solver calls; ba lượt solver **0,891 / 0,404 /
0,412 giây**, median **0,412 giây** (SciPy 1.17.1). CLI đã publish cùng
quyết định; parity 10×10 với DP cũ và 48 dispatch/resource/API tests đạt.
Timing chỉ gồm allocation, không gồm process startup, đọc/hash inputs hoặc
publication. Evidence: `outputs/tests/limits-milp-benchmark.json`; đây là
fixture tài nguyên giả định, không đo chất lượng phân bổ thực tế.

## Complete executable code

- `pipeline/cctv/dispatch.py`: demand contracts, source/review binding, solver,
  assignment/unmet reasons, SHA-bound report, atomic state và resume.
- `configs/cctv.demands.simulation.json`: 3 nhu cầu giả định.
- `configs/cctv.demands.events.json`: event request template rỗng.
- `test_cctv_dispatch.py`: independent brute-force và private legacy oracle
  parity (10×10 và varied small cases), competing 25×25, boundary 100,
  mask >53-bit, tiny travel, fallback, fail-closed certification, cap/reserve,
  skill/review và atomic publication.
- `test_cctv_dispatch_api.py`: large report read-only, feasibility/whole-team/
  reserve validation, stale resource/review snapshots và provenance gates.

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
  --output outputs\demo-data\dispatch-simulation-v2

# Chạy lại nguyên command để kiểm tra resumed=true và không chạy solver.
Get-Content outputs\demo-data\dispatch-simulation-v2\dispatch_report.json

# Event package hiện có rỗng: không tự bịa demand từ footage.
& $python -m pipeline.cctv.dispatch `
  --manifest configs\cctv.demo.json `
  --resources configs\cctv.resources.simulation.json `
  --demands configs\cctv.demands.events.json `
  --events-dir outputs\demo-data\incidents-draft-v1 `
  --review-dir outputs\demo-data\incident-reviews-v1 `
  --output outputs\demo-data\dispatch-events-empty-v2

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
  --output outputs\demo-data\dispatch-review-gate-v2

& $python scripts\run_python_logged.py --log outputs\tests\dispatch-tests.txt -- `
  -m unittest -v test_cctv_dispatch test_cctv_dispatch_api
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
