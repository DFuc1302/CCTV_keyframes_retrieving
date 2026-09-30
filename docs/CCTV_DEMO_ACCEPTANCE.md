# T7.3 — kiểm thử demo đầu-cuối và đo runtime sau tích hợp

## Thiết kế và phạm vi

`scripts/verify_cctv_demo.py` là client HTTP loopback, không nạp model và không
ghi review. Nó kiểm tra UI assets, readiness/index/code của server, truy hồi
text/image với locator độc lập từ SQLite, ảnh hợp lệ, Range video cho sáu
nguồn, ROI/windows, event packages và dispatch/review gates.
Các URL asset phải khớp ID nội bộ trước khi fetch; client tắt proxy môi trường
và redirect. Không gửi dữ liệu tới máy chủ ngoài.

Đây là recipe nghiệm thu **demo repo hiện tại**: yêu cầu các package
`incidents`, `congestion`, `demo`, `simulation`, `events_empty`, `review_gate`.
Không dùng nó để kết luận chất lượng event/relevance hoặc nguồn lực thật.
Event smoke limit 50/package; package lớn hơn sẽ fail thay vì kiểm tra thiếu.

CLI snapshot SHA/mtime của raw, config, index, code/assets và các output đang
dùng, kiểm tra lại sau HTTP. Chỉ lưu report khi toàn bộ checks đạt, vào thư
mục mới dưới `outputs`. Mỗi lần nghiệm thu phải dùng output mới; không dùng
report cũ như phép kiểm tra server hiện tại. CLI không kiểm tra playback của
trình duyệt; browser checks ghi riêng, không tự suy từ HTTP Range.

Benchmark sử dụng công cụ T6.2 và cùng workload 6 text/6 image, top-12,
warmup 2/case, repeats 10/case. Đo HTTP response wall time, bỏ file read,
validation, cold start và warmup. Chỉ một client serial; không chặn client
khác. Report/checkpoint mới giữ nguyên phép đo T6.2.

## Code và lệnh PowerShell

Source đầy đủ: `scripts/verify_cctv_demo.py`. Trong terminal đang chạy server:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Start-CctvDemo.ps1
```

Terminal thứ hai:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m scripts.verify_cctv_demo `
  --manifest configs\cctv.demo.json `
  --index-dir outputs\demo-data\search-index `
  --output outputs\demo-data\acceptance-t7-v3

.\.venv\Scripts\python.exe -m pipeline.cctv.benchmark_runtime `
  --manifest configs\cctv.demo.json `
  --workload configs\cctv.runtime-benchmark.demo.json `
  --index-dir outputs\demo-data\search-index `
  --output outputs\demo-data\runtime-benchmark-t7-v2 `
  --url http://127.0.0.1:8080
```

Đổi output khi muốn đo mới. Benchmark hoàn chỉnh chạy lại vào cùng output
là resume đọc report đã lưu, không phải mẫu latency mới. Muốn test core,
dừng demo bằng Ctrl+C trong terminal server rồi chạy `scripts\Test-Windows.ps1`.
`test_cctv_demo_acceptance.py` dùng HTTP fixtures: luồng đầy đủ, locator sai,
asset ngoài host, thiếu Range, stage thiếu/cap sai và review không khớp.

## Kết quả cục bộ 2026-10-01

HTTP checks: 7.232 keyframes, 6 video Range, 30 traffic windows;
incidents/congestion 0 và demo 1; dispatch simulation 1 assignment/2 unmet,
events_empty 0/0, review_gate 0/1. Không tạo journal thiếu hoặc đổi review.
Report final: `outputs/demo-data/acceptance-t7-v2/acceptance_report.json`;
100 raw/config/index/code/assets giữ hash/mtime. Bộ Windows đạt 208 core
tests, pip check, compile và CUDA FP16/NMS đạt. Kiểm chứng local:
`outputs/demo-data-review/acceptance-t7-verification.json`.

Browser đã thử text search trả 12 kết quả, mở N002-V003 tại 488,014 giây;
traffic N001-V001 mở evidence tại 30,037 giây; event frame 900 mở 35,996 giây.
Video readyState=4 ở các mốc đã kiểm tra. Dispatch review gate còn
`needs_more_evidence` revision 3, 0 assignment/1 unmet. Console không lỗi.
Image search kiểm tra qua HTTP, không ghi đã thử upload trên browser.

| Query | Mẫu đo | P50 (ms) | P95 (ms) | Mean (ms) |
| --- | ---: | ---: | ---: | ---: |
| Text | 60 | 49,76 | 53,78 | 49,69 |
| Image | 60 | 147,91 | 167,64 | 150,29 |

24 warmup + 120 request đo hợp lệ, lỗi=0. Model lifetime allocator peak:
allocated 851,91 MiB, reserved 862 MiB; không phải tổng VRAM hoặc peak theo
request. `outputs/demo-data/runtime-benchmark-t7-v1/runtime_report.json`
gắn app/index/model/code hiện tại. Resume hoàn chỉnh đã kiểm tra.
Hai lần đo T6.2/T7.3 không kiểm soát đầy đủ nhiệt độ/power/background load;
không kết luận tác động nhân quả của UI tới latency từ hai bảng này.

## Commit và bước tiếp theo

```powershell
git add scripts/verify_cctv_demo.py scripts/Test-Windows.ps1 test_cctv_demo_acceptance.py docs/CCTV_DEMO_ACCEPTANCE.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md docs/CCTV_RUNTIME_BENCHMARK.md README.md
git commit -m 'test: add CCTV demo acceptance checks and refresh runtime results'
git push origin main
```

M7 có nghiệm thu kỹ thuật cục bộ; nhãn độc lập, ROI/calibration và chạy trên
máy thứ hai vẫn chưa nghiệm thu. Bước chuẩn bị tiếp theo: bộ mẫu giúp người
dùng duyệt ROI và gán nhãn độc lập; không tự phê duyệt hoặc tạo ground truth.
