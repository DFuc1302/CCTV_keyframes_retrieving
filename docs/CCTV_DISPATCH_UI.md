# T7.2 — UI phân bổ mô phỏng chỉ đọc

## Concept và kiến trúc

T5.2 dùng sparse MILP ưu tiên → tổng di chuyển → nhân lực, giới hạn 100
đội/100 yêu cầu; certificate số học và oracle DP nhỏ được mô tả tại
`CCTV_DISPATCH.md`. T7.2 nối reports vào FastAPI/UI; GET chỉ đọc, không
chạy lại solver hoặc nạp model. Các đội và khu vực đều giả định.

`app/cctv_dispatch.py` đọc checkpoint/report, kiểm tra checksum, manifest,
resource config, demand config và snapshot event/review. Event nguồn phải
khớp camera/video/raw SHA của retrieval. Nếu journal xuất hiện, đổi revision
hoặc cấu hình thay đổi, báo cáo cũ không còn khả dụng; cần chạy CLI T5.2 với
output mới. Kiểm tra khả thi gồm đội sẵn sàng, kỹ năng, khu vực, cạnh di
chuyển có hướng, deadline, không dùng lại đội, cap và dự phòng. API không
tính lại tính tối ưu; thuật toán đã có kiểm thử oracle tại T5.2.

`app/cctv.py` bổ sung hai GET routes:

- `/cctv/dispatch`: tình trạng từng gói, phân biệt thiếu/lỗi với gói 0 yêu cầu.
- `/cctv/dispatch/{package_id}`: report đã kiểm tra, nhân lực, assignments,
  unmet reasons, SHA snapshot; không trả đường dẫn file/cấu hình nội bộ.

Mỗi gói có tối đa 100 yêu cầu, hiện hiển thị trọn gói không phân trang.
Lỗi gói riêng trả 503, ID không đăng ký trả 404; retrieval không bị vô hiệu
hóa. Các GET routes chỉ đọc; tính proposal workflow là thao tác riêng.
`create_app(dispatch_store=...)` cho phép inject paths ở deployment/test;
manifest và resources mặc định có thể đổi bằng `$env:CCTV_MANIFEST` và
`$env:CCTV_RESOURCE_CONFIG`. Các report/package path mặc định nằm dưới repo.

Tab **Phân bổ mô phỏng** (`static/cctv/dispatch.js`) hiển thị roster, available,
dispatch cap, assigned, available remaining, reserve; từng đội và lý do chưa
xếp. Dữ liệu động qua `textContent`, không dùng HTML từ report. Request token
ngăn response cũ ghi đè khi đổi kịch bản. Tabs hỗ trợ Arrow/Home/End.

## Ba gói demo

| ID | Output | Kết quả dự kiến |
| --- | --- | --- |
| `simulation` | `outputs/demo-data/dispatch-simulation-v2` | 1 assignment, 2 unmet; xếp 2/4 người sẵn sàng, giữ dự phòng 2 |
| `events_empty` | `outputs/demo-data/dispatch-events-empty-v2` | 0 request, 0 assignment; không tự tạo journal |
| `review_gate` | `outputs/demo-data/dispatch-review-gate-v2` | Case synthetic đang `needs_more_evidence`, 0 assignment, 1 unmet |

Gói review gate dùng demand file local
`outputs/demo-data-review/dispatch-event-request.json` và journal demo hiện có.
Không tự ghi/đổi quyết định review để làm cho case được phân bổ.
Clone chỉ có source, chưa rebuild output sẽ hiển thị gói chưa khả dụng.
Tạo reports theo `CCTV_DISPATCH.md`; không chỉnh checksum để bỏ qua lỗi stale.

## Chạy và kiểm thử trên PowerShell

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Test-Windows.ps1
.\scripts\Start-CctvDemo.ps1
```

Nếu server đang chạy phiên bản cũ, dừng bằng Ctrl+C trong terminal đã mở
server rồi khởi động lại; tránh nạp hai CLIP instance hoặc chiếm trùng 8080.
Mở `http://127.0.0.1:8080/`, chọn tab Phân bổ mô phỏng và lần lượt ba kịch bản.

Trong terminal PowerShell thứ hai:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8080/cctv/dispatch'
Invoke-RestMethod 'http://127.0.0.1:8080/cctv/dispatch/simulation'
Invoke-RestMethod 'http://127.0.0.1:8080/cctv/dispatch/review_gate'
```

`test_cctv_dispatch_api.py` dùng fixtures trong thư mục tạm: read-only/no-solver,
cap/reserve, corruption, source scope, cấu hình stale, báo cáo không khả thi,
gói rỗng/thiếu, journal xuất hiện và revision thay đổi. Review trong tests chỉ
ghi vào fixtures, không dùng journal demo hiện có. Bộ Windows bao gồm module này.

## Git

```powershell
git add app/cctv.py app/cctv_dispatch.py static/cctv/index.html static/cctv/traffic.js static/cctv/dispatch.js scripts/Test-Windows.ps1 test_cctv_dispatch_api.py docs/CCTV_DISPATCH_UI.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md README.md
git commit -m 'feat: expose read-only simulated dispatch proposals in CCTV demo'
git push origin main
```

T7.3 tiếp theo: nghiệm thu demo đầu-cuối và đo runtime với code hiện tại/output
mới. Benchmark T6.2 vẫn là đo lịch sử của code cũ. ROI/nhãn độc lập, vị trí và
nguồn lực thật, cũng như chạy trên máy thứ hai, còn mở.
