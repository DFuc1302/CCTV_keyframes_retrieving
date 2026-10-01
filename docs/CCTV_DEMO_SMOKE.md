# T9.1 — chạy kiểm thử HTTP demo bằng một lệnh

## Concept / architecture

T7.3 đã có client kiểm tra HTTP đầu-cuối. T9.1 bổ sung quản lý vòng đời server
để chạy thử từ một terminal, phục vụ mục tiêu kiểm tra demo chạy được khi
ROI và nhãn thật vẫn pending. Không thay model/index hoặc logic nhận diện.

`scripts/run_cctv_demo_smoke.py` reserve socket loopback trước nạp model,
truyền socket đó vào Uvicorn để tránh bind race. Một process, một thread
server, một worker, không reload: một CLIP instance FP16 khi dùng CUDA.
Chờ `/ready` có timeout, gọi client T7.3, rồi yêu cầu shutdown và join thread
trong finally, kể cả khi check thất bại. Không tạo child server mồ côi hoặc
dừng server khác theo PID/port. Khi CLI kết thúc, process giải phóng model.

`-ExistingServer` dùng server đang có mà không nạp CLIP hoặc dừng server đó.
Không mở server khác trong lúc chạy managed smoke; nếu demo đang chạy, dùng
existing mode trên đúng port. Port bận mặc định là lỗi trước inference.
Đây là HTTP integration, không là browser playback, chất lượng sự kiện,
duyệt ROI hoặc phân bổ nhân lực thực tế. Không chạy benchmark latency mới.

## Code đầy đủ / PowerShell run and test

Source: `scripts/Test-CctvDemo.ps1` và `scripts/run_cctv_demo_smoke.py`.
Yêu cầu venv, model cache và toàn bộ outputs demo T7.3 đã dựng; không tự tải
model hoặc dựng lại pipeline. Chạy từ Windows PowerShell:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Test-CctvDemo.ps1
```

Lệnh tự đặt tên output/log bằng timestamp và UUID; chờ readiness tối đa
120 giây rồi kiểm tra. Log đầy đủ dưới `outputs/tests`, report tại
`outputs/demo-data/demo-smoke-<run-id>/acceptance_report.json`. Mỗi run dùng
output mới vì đây là kiểm tra server hiện tại, không resume report cũ.
Console in đường dẫn log trước khi khởi động và kết quả cuối sau hoàn tất.
Có thể đọc log đó bằng `Get-Content -LiteralPath ... -Wait` trong terminal khác.

Nếu cổng 8080 thuộc app khác, chọn port trống:

```powershell
.\scripts\Test-CctvDemo.ps1 -Port 8087 -StartupTimeout 180
```

Nếu demo đã chạy bằng `Start-CctvDemo.ps1`, dùng đúng port và tránh model thứ hai:

```powershell
.\scripts\Test-CctvDemo.ps1 -ExistingServer -Port 8080
```

Không thay đổi server đang chạy. Option `-Device cpu` dành cho managed mode
nếu muốn chạy CPU, có thể chậm hơn; existing mode giữ device của server.
Lệnh này kết thúc và dừng managed server; để mở UI lâu dài dùng launcher cũ:

```powershell
.\scripts\Start-CctvDemo.ps1
```

Kiểm thử fixtures không cần nạp weights:

```powershell
& .\.venv\Scripts\python.exe scripts/run_python_logged.py `
  --log outputs/tests/demo-smoke-unit.txt -- `
  -m unittest -v test_cctv_demo_smoke test_powershell_scripts
Get-Content outputs/tests/demo-smoke-unit.txt -Tail 18
.\scripts\Test-Windows.ps1
```

Wrapper capture stderr bằng Python để tránh NativeCommandError trên PS5.1.
Startup/check/shutdown failures trả nonzero; report acceptance chỉ chứng minh
HTTP checks, kết quả CLI phải thành công mới bao gồm managed shutdown đạt.
Timeout shutdown 15 giây; nếu thread chưa dừng, CLI báo lỗi và thoát process.
Raw/config/index/review journals chỉ đọc; output bị chỉnh hoặc nonempty không
được ghi đè. Client T7.3 kiểm tra provenance/locator và input SHA/mtime.

## Kết quả / Git

Managed smoke thật đã chạy CUDA/FP16 trên 8087: CLIP 768D, HTTP passed,
100 protected files giữ SHA/mtime, server shutdown thành công. Report local:
`outputs/demo-data/demo-smoke-t9-v1/acceptance_report.json`. Không có phép
đánh giá nhãn thật; ROI/annotations vẫn pending. Tests mới kiểm tra owned
server cleanup, existing server, failure paths và Windows parser/busy port.

Windows core suite đạt **244 tests**, pip check/compile và CUDA FP16/NMS đạt.
Cổng 8087 bind được lại sau smoke, xác minh socket đã giải phóng.

```powershell
git add scripts/Test-CctvDemo.ps1 scripts/run_cctv_demo_smoke.py scripts/Test-Windows.ps1 test_cctv_demo_smoke.py test_powershell_scripts.py docs/CCTV_DEMO_SMOKE.md docs/CCTV_PROGRESS.md docs/CCTV_PROJECT_REPORT.md
git commit -m "test: add one-command managed CCTV demo smoke"
git push origin main
```
