# T5.1 — Resource configuration, simulation only

## Concept / architecture rationale

T5.1 tạo snapshot cấu hình để T5.2 có thể thử bộ gợi ý phân bổ. Chưa có vị
trí camera, đội tuần tra hoặc thời gian di chuyển thực tế được xác nhận.
Hai zone và ba team trong `configs/cctv.resources.simulation.json` đều là
**giả định**; N001/N002 chỉ được map vào zone trong kịch bản mô phỏng riêng.
Không sửa manifest CCTV, không suy vị trí từ tên/overlay và không gán GPS.

Schema v1 chỉ cho phép `mode=simulation`. `operational_ready` luôn false.
`simulation_ready` chỉ có nghĩa mapping và một số ràng buộc cấu hình đủ để
thử bài toán; không chứng minh có phương án đáp ứng một tập demand cụ thể.
`assignments` hiện rỗng; T5.1 không đọc event, tự xác nhận incident hoặc tạo
lệnh điều động.

Các bảng cấu hình:

| Thành phần | Ý nghĩa và kiểm tra |
| --- | --- |
| zones | ID duy nhất, tên khu vực giả định; không có tọa độ địa lý |
| camera_bindings | Mỗi canonical camera đúng một lần; simulated có zone hợp lệ, unknown phải zone=null |
| units | Team không chia nhỏ, quân số integer 1–20; available/busy/offline; skills và allowed_zones rõ ràng |
| travel_times | Chi phí giây giả định có hướng; không suy chiều ngược hoặc đường vòng; cùng home zone coi bằng 0 trong mô hình |
| policy | Ngân sách nhân sự, giới hạn dispatch, quân số dự phòng và yêu cầu event đã confirmed |

`staff_budget` là **số người**, không phải ngân sách tiền. Tổng quân số team
không vượt budget; max_dispatch và reserve không vượt budget. Dự phòng giữ
trong nhóm đang available, không lấy người busy/offline làm dự phòng:

```text
dispatchable_officer_limit = min(max_dispatch_officers,
                                max(0, available_officers - reserve_officers))
```

Config demo: roster=6, available=4, busy=2, reserve=2, max_dispatch=4;
cap thực tế trong snapshot là 2. Một team 2 người có thể vừa cap, hai team
2 người đồng thời sẽ vượt cap. `eligible_without_demand` là kiểm tra từng
cặp team/zone riêng, **không được cộng tất cả các cặp eligible thành assignments**.
Team không tách một người ra để vừa cap; nếu cap=1, team 2 người bị chặn.

Chi phí 90 giây A→B và 120 giây B→A là dữ liệu giả lập để kiểm tra hướng.
Không phải ETA thật, không bao gồm thời gian huy động. Thiếu cặp travel
giữ null và chặn cặp đó. Unknown camera không tự map vào zone gần nhất.
`require_confirmed_events=true` là ràng buộc policy đã validate; T5.2 cần
thực sự kiểm tra trạng thái journal khi nối demand/events vào allocator.

## Complete executable code

- `pipeline/cctv/resources.py`: Pydantic frozen contracts, reference/budget
  validation, summary, safe atomic publication và CLI.
- `configs/cctv.resources.simulation.json`: kịch bản giả định Windows-ready.
- `test_cctv_resources.py`: 13 tests, gồm negative constraints và interruption.

Không tải CV model, không dùng GPU và không xử lý video/frame trong stage
cấu hình. Không đổi requirements. Raw và configs là input chỉ đọc; report
ở output riêng, bind SHA256 manifest/config/code. Resume cùng input/recipe
trả `resumed=true`, không rewrite report/state. Input hoặc code thay đổi thì
dùng output mới. Output chồng raw/config/manifest hoặc foreign folder bị chặn.

## PowerShell run/test commands

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'

& $python -m pipeline.cctv.resources `
  --manifest configs\cctv.demo.json `
  --config configs\cctv.resources.simulation.json `
  --output outputs\demo-data\resources-simulation-v1

# Chạy lại nguyên command trên để kiểm tra resumed=true.
Get-Content outputs\demo-data\resources-simulation-v1\resource_report.json

& $python scripts\run_python_logged.py --log outputs\tests\resource-tests.txt -- `
  -m unittest -v test_cctv_resources
.\scripts\Test-Windows.ps1
```

Tests kiểm tra cap/reserve, chiều travel độc lập, team busy, team indivisible,
roster rỗng, camera unknown, ID/canonical coverage, duplicate/unknown refs,
bool giả integer, mode operational không hợp lệ và review gate không tắt được.
Kiểm tra publication gồm resume hash/mtime, output isolation, corrupt report,
changed config và phục hồi sau interruption. Không dùng footage để gán nhãn.

Report/bằng chứng local: `outputs/demo-data/resources-simulation-v1` và
`outputs/demo-data-review/resource-verification.json`. Dữ liệu/model/index
và report local không push GitHub.

## Git commit / push

```powershell
git add pipeline/cctv/resources.py configs/cctv.resources.simulation.json `
  test_cctv_resources.py scripts/Test-Windows.ps1 docs/CCTV_RESOURCES.md `
  docs/CCTV_PROGRESS.md README_CCTV.md README.md
git commit -m 'feat: validate simulated CCTV zones and patrol resource constraints'
git push origin main
```

Tiếp theo T5.2: demand mô phỏng và bộ gợi ý có lý do, kiểm tra kỹ năng,
địa bàn, chi phí có hướng, mỗi team dùng một lần, cap tổng và reserve. Báo
demand chưa đáp ứng thay vì vượt ngân sách hoặc giả định có đủ người. Event
integration cần review status thật của package; demo flags không được xóa.
