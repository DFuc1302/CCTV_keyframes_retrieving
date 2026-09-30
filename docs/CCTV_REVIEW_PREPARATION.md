# T8.1 — chuẩn bị mẫu duyệt ROI và gán nhãn độc lập

## Concept và thiết kế

Đánh giá rule cần ground truth độc lập, bao gồm đoạn không có sự kiện; review
các ứng viên đã được hệ thống chọn không thể thay thế bước này. T8.1 chỉ
chuẩn bị tư liệu, không gán nhãn hay duyệt ROI thay người dùng.

`scripts/prepare_cctv_review.py` đọc manifest, annotation/split pending, ROI
draft, index và video-preview đã có. Nó chọn tối đa sáu keyframes theo thứ
tự phân bố đều trong mỗi khoảng PTS `[start,end)`, giữ nguyên bytes JPEG và
locator nguồn. Không chạy CLIP/detector, không đọc predictions/journal.
Source/preview/index và đầu vào được kiểm tra checksum; output riêng có
checkpoint, progress/per-frame errors và resume giữ hash/mtime. Đầu vào hoặc
artifact thay đổi thì báo lỗi và giữ file đã sửa, không ghi đè.

**Ảnh sạch** nghĩa là không bổ sung bbox/rule/ROI; overlay camera vốn có trong
footage vẫn giữ nguyên. Không suy vị trí/thời gian tuyệt đối từ overlay.
Ảnh sạch và trang không có predictions chưa tự chứng minh review độc lập.
Người đã xem predictions của cùng khoảng cần ghi rõ exposure; không được
khai `blind_to_predictions=true` nếu điều đó không đúng.

Bộ demo dùng sáu khoảng `[30,40)` từ template hiện có, tổng 60 video-seconds.
Chúng đã được dùng chạy thử pipeline; không phải lấy mẫu đại diện/ngẫu nhiên
và chưa đủ để kết luận chất lượng tai nạn/tắc đường. Cần bổ sung video/giờ/
điều kiện đa dạng và xác minh recording groups trước khi freeze development/test.
Không tự gán nhóm mới theo tên file hoặc chia tập khi grouping chưa xác nhận.

## Code và cấu trúc output

Source đầy đủ: `scripts/prepare_cctv_review.py`.

```text
review-preparation-v1/
  labeling/index.html              ảnh sạch + video source, không có ROI draft
  labeling/frames/<job>/*.jpg       tối đa 6 JPEG nguyên bản mỗi khoảng
  labeling/sheets/<job>.png         contact sheets với PTS/source frame ordinal
  roi/index.html                   vùng ROI nháp, xem riêng với người duyệt ROI
  roi/<job>.png                    polygon pending trên ảnh sạch
  forms/annotations.pending.json   copy template, 0 interval reviewed
  forms/splits.pending.json        group unverified, split unassigned
  forms/roi.pending.json           polygon draft, 0 camera approved
  review_manifest.json             input SHA và locator từng ảnh
  review_state.json                artifact SHA, progress và trạng thái publication
```

HTML là tư liệu chỉ đọc, không có submit/review API. Video lấy từ demo
`http://127.0.0.1:8080/cctv/video/...`; nút đầu khoảng seek theo source PTS
cộng offset remux. Không copy video hoặc tự lưu quyết định người xem.
Phải xem toàn bộ khoảng video và thêm context nếu cần; contact sheets không
đủ để kết luận tai nạn hoặc tính liên tục của congestion.

## PowerShell chạy và kiểm thử

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m scripts.prepare_cctv_review `
  --manifest configs\cctv.demo.json `
  --annotations configs\cctv.annotations.pending.json `
  --splits configs\cctv.splits.pending.json `
  --rois configs\cctv.roi-draft.json `
  --index outputs\demo-data\search-index `
  --previews outputs\demo-data\video-preview `
  --output outputs\demo-data\review-preparation-v1
```

Chạy lại nguyên command: sáu interval skipped, không render/rewrite. Khi
thay template/code/input, chọn output mới. Dừng server demo trước khi chạy
`scripts\Test-Windows.ps1` để tránh import runtime PyTorch đồng thời.

Mở HTML trực tiếp trong browser, hoặc phục vụ riêng bundle ở terminal khác:

```powershell
.\.venv\Scripts\python.exe -m http.server 8090 --bind 127.0.0.1 `
  --directory outputs\demo-data\review-preparation-v1
```

Mở `http://127.0.0.1:8090/labeling/index.html`; ảnh vẫn xem được khi demo
8080 chưa chạy. Muốn xem video, chạy `scripts\Start-CctvDemo.ps1` ở terminal
khác. Trang ROI: `http://127.0.0.1:8090/roi/index.html`.

## Quy trình review do con người thực hiện

1. Hai reviewer ghi nhận riêng, xem footage trước khi xem output rule/ROI.
   Lưu notes riêng về camera/video, PTS, loại sự kiện, mức chắc chắn, context
   đã xem; không đưa notes/predictions của người khác cho reviewer còn lại.
2. Phân biệt `moving_to_stop`, `road_incident`, `accident`, `congestion`.
   Xe dừng tại đèn không tự là tai nạn; chỉ đánh dấu accident khi footage
   cung cấp bằng chứng quan sát được. Nếu 10 giây/context không đủ, giữ
   unknown hoặc thu thập thêm context, không ép negative.
3. Adjudicate bất đồng sau review riêng. Chỉ interval đã xem đầy đủ ontology
   và đủ provenance mới thành `reviewed`; `events=[]` khi reviewed mới có
   nghĩa negative. Điền reviewer names thật, timestamp có timezone và trạng
   thái blind đúng thực tế. Validator không xác thực danh tính/độc lập.
4. Người duyệt ROI kiểm tra polygon trên cả ba video mỗi camera, phạm vi
   đường/vùng không quan sát được, tọa độ chuẩn hóa và thứ tự vertex. Chỉ
   approved khi có reviewer/time thật; draft vẫn pending nếu chưa quyết định.
5. Xác minh recording/session groups và trùng bản sao/đoạn thời gian. Sau đó
   freeze split/protocol; không dùng test để chọn threshold.

Làm việc trên bản copy ở folder riêng, giữ bundle và config gốc:

```powershell
$work = 'outputs\annotations-work-v1'
if (Test-Path -LiteralPath $work) { throw 'Choose a new working folder.' }
New-Item -ItemType Directory -Path $work | Out-Null
Copy-Item -LiteralPath 'outputs\demo-data\review-preparation-v1\forms\annotations.pending.json' -Destination "$work\annotations.json"
Copy-Item -LiteralPath 'outputs\demo-data\review-preparation-v1\forms\splits.pending.json' -Destination "$work\splits.json"
Copy-Item -LiteralPath 'outputs\demo-data\review-preparation-v1\forms\roi.pending.json' -Destination "$work\roi.json"
```

Không tự chuyển status để có metrics. Đánh giá sau khi có nhãn thật theo
`CCTV_BENCHMARK.md`; dùng output benchmark mới, giữ metrics pending hiện có.
ROI thay đổi cũng cần output windows/events mới, không sửa artifact cũ.

## Kết quả kiểm chứng cục bộ 2026-10-01

Sáu interval `[30,40)`: 36 JPEG giữ bytes nguyên bản, sáu contact sheets và
sáu ROI overlays pending. Forms vẫn 0 reviewed, 0 approved, group unverified
và split unassigned. 59 input files được freeze; resume skip sáu interval,
55 bundle artifacts giữ SHA/mtime. Browser tải đủ sáu sheets, nút đầu khoảng
N001-V001 seek 30,000 giây, video readyState=4, console không lỗi.
Sáu raw videos và upstream artifacts giữ SHA/mtime theo kiểm chứng hiện có.
Không chấm metrics mới hoặc thay journal.

Windows core suite: 214 tests đạt, pip check/compile/CUDA FP16/NMS đạt.
Một lần chạy fixture riêng lúc CLIP server còn mở gặp WinError 1455 khi
import DLL PyTorch; dừng server do agent khởi động và chạy lại bộ Windows
thành công, không đổi paging/system config. Server sau đó được mở lại.
Kết quả: `outputs/demo-data-review/review-preparation-verification.json`.

## Git

```powershell
git add scripts/prepare_cctv_review.py test_cctv_review_preparation.py scripts/Test-Windows.ps1 docs/CCTV_REVIEW_PREPARATION.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md README.md
git commit -m 'feat: prepare pending CCTV ROI and independent annotation review aids'
git push origin main
```

Không push JPEG/PNG/footage, người review hoặc nhãn thực trong outputs lên
repo. Bước tiếp theo là review thật hoặc chuẩn bị thêm khoảng đa dạng; bộ
này không thay thế nghiệm thu chất lượng hay chạy trên máy thứ hai.
