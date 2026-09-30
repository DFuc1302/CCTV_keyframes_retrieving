# T4.1 — Event evidence và review

## Concept và kiến trúc

Module này chuyển **ứng viên** từ traffic rule thành package có bằng chứng,
không tự xác nhận sự cố hoặc nhận diện tai nạn. ROI vẫn pending, ngưỡng chưa
hiệu chỉnh, chưa có nhãn sự kiện độc lập. Review một case demo không duyệt ROI
và không làm kết quả đủ điều kiện điều phối cảnh sát.

Một episode gồm các cửa sổ `slow_dense`/`congestion_candidate` liên tiếp của
cùng video. Chỉ xuất event khi episode đạt persistence; thời điểm bắt đầu
bao gồm các cửa sổ slow/dense trước khi đạt ngưỡng. Gaps hoặc cửa sổ không đủ
bằng chứng cắt episode. Điều này tránh tạo nhiều event trùng nhau mỗi hai giây.
Không suy vận tốc vật lý, vị trí hoặc thời gian ghi hình tuyệt đối.

`events.jsonl` lưu schema v1: ID xác định từ SHA256 nội dung, camera/video/run,
khoảng PTS tương đối `[start_sec,end_sec)`, frame đầu/cuối thực sự có trong
tracking DB, segment ID, lý do, provenance và các cờ demo/uncalibrated.
Locator gồm `camera_id`, `video_id`, `timestamp_sec`, `frame_idx`; không thay
đổi keyframe maps, FAISS hoặc metadata cũ. Frame bằng chứng có thể nằm giữa
hai keyframe 2 FPS; dùng video seek theo timestamp, không giả định có JPEG
keyframe trùng frame_idx.

Importer xác minh upstream publication/hash, manifest, camera/run và clip
bounds trước khi chạy. SQLite tracking được mở `mode=ro`. Chạy bằng CPU,
không tải model và không dùng thêm VRAM. Checkpoint theo video, tqdm và log
lỗi theo video; lỗi evidence không được xuất package hoàn tất. Resume cùng
recipe/input skip video đã hoàn tất; package complete không rewrite. Input
hoặc code thay đổi yêu cầu output mới. Ledger giới hạn 64 MiB/10.000 records.

Review nằm trong thư mục riêng: `review_manifest.json` bind chính xác package;
`reviews.db` chỉ append lịch sử có hash chain và revision. Mỗi quyết định
cần reviewer, reason, timestamp có timezone và `expected_revision`. Revision
cũ bị từ chối để tránh ghi đè quyết định đang được người khác xem.

```text
pending_review -> confirmed | rejected | needs_more_evidence
needs_more_evidence -> confirmed | rejected | needs_more_evidence
confirmed/rejected -> needs_more_evidence (mở lại với lý do)
```

Case có `demo_only` hoặc `synthetic_event` luôn mang scope `demo_review`, kể
cả khi quyết định là confirmed. Các cờ gốc và `operational_ready=false` giữ
nguyên. Đây là nhật ký cục bộ phục vụ coursework; reviewer là tên nhập vào,
chưa có đăng nhập, phân quyền hoặc chữ ký chống sửa bởi người có quyền sửa
database. Hash chain phát hiện thay đổi nội dung/liên kết trong các record
đang lưu, không chứng minh lịch sử chưa bị xóa toàn bộ hoặc cắt phần cuối.
Không sửa/xóa reviews để “reset”; dùng package/journal demo mới.

## Code hoàn chỉnh

- `pipeline/cctv/signals.py`: contract chung, API cũ vẫn import được `Signal`.
- `pipeline/cctv/events.py`: schema, evidence validation, package và CLI.
- `pipeline/cctv/event_review.py`: list, CAS và lịch sử review.
- `test_cctv_events.py`: kiểm thử CPU với tracking/windows tổng hợp.

Chưa thêm event queue vào UI; tab Giao thông và retrieval tiếp tục sử dụng
output cũ. Schema hiện chỉ nhận `congestion_candidate` và `demo_case`;
T4.2 bổ sung schema v2 `suspected_road_incident` và rule move-then-stop trong
[`CCTV_INCIDENTS.md`](CCTV_INCIDENTS.md); chưa có schema/chẩn đoán tai nạn.

## PowerShell: chạy và kiểm thử

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'

# Package ứng viên từ dữ liệu hiện có: dự kiến 0 events, không thêm case giả.
& $python -m pipeline.cctv.events `
  --manifest configs\cctv.demo.json `
  --windows-dir outputs\demo-data\traffic-draft-v1 `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\events-v1

& $python -m pipeline.cctv.event_review list `
  --events-dir outputs\demo-data\events-v1 `
  --review-dir outputs\demo-data\event-reviews-v1

# Bài tập review riêng: tình huống tổng hợp trên frame tham chiếu thật.
# Không phải tai nạn/tắc đường được phát hiện trong footage.
& $python -m pipeline.cctv.events `
  --manifest configs\cctv.demo.json `
  --windows-dir outputs\demo-data\traffic-draft-v1 `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\events-demo-v1 `
  --demo-video N001-V001 --demo-start-sec 30 --demo-end-sec 36

$demoEvent = Get-Content outputs\demo-data\events-demo-v1\events.jsonl |
  ForEach-Object { $_ | ConvertFrom-Json } |
  Where-Object { $_.synthetic_event } |
  Select-Object -First 1

# Mỗi lượt chạy example dùng một journal mới; tên reviewer chỉ dùng demo.
$demoReviewDir = Join-Path 'outputs\demo-data' ('example-reviews-' + [Guid]::NewGuid().ToString('N'))
& $python -m pipeline.cctv.event_review review `
  --events-dir outputs\demo-data\events-demo-v1 `
  --review-dir $demoReviewDir `
  --event-id $demoEvent.event_id --decision needs_more_evidence `
  --reviewer 'demo-reviewer' --reason 'Synthetic exercise; request additional evidence.' `
  --expected-revision 0

& $python -m pipeline.cctv.event_review list `
  --events-dir outputs\demo-data\events-demo-v1 --review-dir $demoReviewDir

# Chạy lại importer để kiểm tra resume; không đổi options/output.
& $python -m pipeline.cctv.events `
  --manifest configs\cctv.demo.json `
  --windows-dir outputs\demo-data\traffic-draft-v1 `
  --tracking-dir outputs\demo-data\tracking-clips `
  --output outputs\demo-data\events-v1

& $python scripts\run_python_logged.py --log outputs\tests\event-tests.txt -- `
  -m unittest -v test_cctv_events
.\scripts\Test-Windows.ps1
```

Các output/local CCTV không push lên GitHub. Raw, CLIP/index, detections,
tracking và traffic publications giữ nguyên. Bằng chứng cục bộ của lượt chạy:
`outputs/demo-data-review/event-verification.json`.

## Git

```powershell
git add pipeline/cctv/signals.py pipeline/cctv/events.py pipeline/cctv/event_review.py `
  app/cctv_traffic.py test_cctv_events.py scripts/Test-Windows.ps1 `
  docs/CCTV_EVENTS.md docs/CCTV_PROGRESS.md README_CCTV.md README.md
git commit -m 'feat: add CCTV event evidence and auditable review workflow'
git push origin main
```

T4.2 đã có baseline theo thời gian và fixtures, xem `CCTV_INCIDENTS.md`.
Dữ liệu thật vẫn cần nhãn độc lập trước khi báo cáo precision/recall hoặc
gọi kết quả là phát hiện tai nạn.
