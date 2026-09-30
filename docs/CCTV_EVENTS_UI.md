# T7.1 — sự kiện, review state và video evidence trên demo

## Thiết kế

Tab **Sự kiện & evidence** mở rộng UI bằng API chỉ đọc, không nạp model
thứ hai hoặc ghi review. Decision vẫn qua CLI T4.1 với reviewer, reason và
expected revision; T7.2 sẽ nối phân bổ mô phỏng.

`app/cctv_events.py` đọc sealed events v1/v2 bằng loader hiện có, đối chiếu
tracking source SHA với raw identity đã được retrieval startup kiểm tra.
Camera/video phải khớp metadata phục vụ. Publication checksum được kiểm tra
trước/sau request; journal mở SQLite mode=ro, kiểm tra binding và hash chain.
Review được đọc lại mỗi request. Journal lỗi trả unavailable, không tự đổi
thành pending. Optional event failure không làm mất retrieval readiness.

Evidence giữ source frame ordinal/PTS và seek offset của preview; UI mở
video bằng viewer hiện có, không tạo ảnh raw mới. Payload không xuất source
paths/protected roots; lỗi chi tiết chỉ ở log server. TextContent hiển thị
rationale/review để không chèn HTML từ dữ liệu; request tokens loại response
cũ khi đổi lựa chọn, pagination 20/page và tab navigation bằng phím.

| Package | Output/journal | Nội dung |
| --- | --- | --- |
| incidents | `incidents-draft-v1` / `incident-reviews-v1` | Rule nghi sự cố v2 trên footage |
| congestion | `events-v1` / `event-reviews-v1` | Rule congestion v1 trên footage |
| demo | `events-demo-v1` / `events-demo-reviews-v1` | Gói chạy thử có case tổng hợp |

Từng card giữ cờ synthetic/demo/uncalibrated/operational=false. Gói demo
có thể chứa cả rule candidates, nên phân biệt theo synthetic_event từng row.
Review confirmed trong demo không chứng minh tai nạn. Gói hợp lệ 0 ứng viên
hiển thị empty state; package unavailable không được gọi là 0 sự kiện.

## Code/API và PowerShell

Code đầy đủ: `app/cctv_events.py`, endpoints trong `app/cctv.py`,
`static/cctv/events.js` và panel trong `static/cctv/index.html`.

- `GET /cctv/events`: catalog, available/count theo package, read_only=true.
- `GET /cctv/events/{package_id}?offset=0&limit=20`: rows/evidence/review;
  limit 1–50, offset không âm. Unknown=404, malformed=422, unavailable=503.
- Không có POST review hoặc endpoint solver/inference trong event UI.

Có thể cấu hình output bằng env vars có đường dẫn tuyệt đối:
`CCTV_INCIDENTS_DIR`, `CCTV_INCIDENT_REVIEWS_DIR`, `CCTV_EVENTS_DIR`,
`CCTV_EVENT_REVIEWS_DIR`, `CCTV_DEMO_EVENTS_DIR`, `CCTV_DEMO_REVIEWS_DIR`.

Sau khi dừng phiên launcher cũ bằng Ctrl+C:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Start-CctvDemo.ps1
```

Terminal khác:

```powershell
Invoke-RestMethod 'http://127.0.0.1:8080/cctv/events'
Invoke-RestMethod 'http://127.0.0.1:8080/cctv/events/demo?offset=0&limit=20'
```

Mở localhost:8080, chọn Sự kiện & evidence. Hai gói rule hiện có 0 ứng viên;
Case tổng hợp hiện needs_more_evidence, revision 3. Evidence 1/2 mở N001-V001
tại source PTS 30,037/35,996 giây, frame 751/900. Kiểm tra lại đọc review
mới, không sửa journal. Tạo package demo theo `CCTV_EVENTS.md` khi chưa có;
không sửa journal hiện có để test UI.

Đóng server trước core suite nếu cần giải phóng RAM/VRAM:

```powershell
.\scripts\Test-Windows.ps1
```

Năm regression tests dùng synthetic/tiny encoder, kiểm tra source/seek,
read-only/live journal, corruption, bounds và lỗi package độc lập retrieval.
**194 core tests đạt**, CUDA FP16/NMS và pip check đạt. Không có nhãn hoặc
quyết định review mới trên footage thật.

Browser kiểm thử gói 0 ứng viên/case tổng hợp, đọc đúng review revision 3,
hai video seek 30,037/35,996 giây, Home/End tab navigation và không có lỗi
JavaScript trong phiên kiểm thử. 14 event/journal artifacts và raw/upstream
giữ hash/mtime. Bằng chứng cục bộ:
`outputs/demo-data-review/events-api-verification.json`.

Code API thay đổi khiến frozen runtime benchmark T6.2 không resume được
với recipe cũ. `runtime-benchmark-v1` giữ nguyên như phép đo lịch sử;
đo code mới cần output mới theo `CCTV_RUNTIME_BENCHMARK.md`, không sửa checksum
checkpoint cũ để vượt guard.

## Git

```powershell
git diff --check
git add app/cctv.py app/cctv_events.py static/cctv/events.js `
  static/cctv/index.html static/cctv/style.css static/cctv/traffic.js `
  test_cctv_events_api.py scripts/Test-Windows.ps1 docs/CCTV_EVENTS_UI.md `
  docs/CCTV_PROGRESS.md README_CCTV.md docs/CCTV_PROJECT_REPORT.md
git commit -m 'feat: expose read-only CCTV event evidence and review UI'
git push origin main
```
