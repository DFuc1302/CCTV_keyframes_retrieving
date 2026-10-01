# M11 — xử lý demo qua một UI

Dữ liệu của project là demo. Mục tiêu M11 là người dùng chọn video, chuẩn bị,
chạy pipeline, tra cứu, review và xuất báo cáo trong cùng giao diện. M8 vẫn
pending; review demo một người không tạo ground truth độc lập.

## Mở và sử dụng

Sau khi môi trường Windows được cài bằng `scripts/Setup-Windows.ps1 -Vision`,
double-click **Open-CctvDemo.cmd** tại thư mục repo. Launcher mở browser,
tái sử dụng server CCTV trên cổng 8080 hoặc khởi động một server local.
Nếu cổng thuộc ứng dụng khác, launcher báo lỗi và giữ nguyên ứng dụng đó.

1. Bấm **Dùng demo có sẵn**, hoặc **Tạo bộ upload** rồi chọn video H.264.
   Tối đa sáu video, mỗi video 1 GiB. Video gốc không bị sửa.
2. Gán camera; chọn ảnh tham chiếu, click ít nhất ba đỉnh ROI cho từng camera.
   Nút **Toàn ảnh để chạy thử** hỗ trợ kiểm thử nhanh bằng bàn phím.
   Khoảng tracking dùng chung cho mọi video: mặc định 10 giây, tối đa 60 giây.
   Lưu chuẩn bị. ROI giữ trạng thái pending.
3. Bấm **Chạy toàn bộ pipeline**. Nếu thiếu weights, dùng nút tải mô hình.
   Theo dõi tiến độ, nhật ký; **Dừng** giữ checkpoint và **Thử lại** tiếp tục
   cùng recipe. Muốn đổi cấu hình, lưu chuẩn bị rồi chạy một run mới.
4. Mở kết quả trong các tab: tìm keyframe bằng text/ảnh, xem video tại mốc
   tương ứng, traffic windows, ROI và video tracking bbox/ID.
5. Trong **Sự kiện & evidence**, chọn case tổng hợp, nhập reviewer/lý do và
   lưu quyết định demo. Tính proposal với số người, ưu tiên và kỹ năng;
   kiểm tra kết quả trong **Phân bổ mô phỏng**.
6. **Tải báo cáo ZIP** chứa summary và các JSON nhỏ hơn hoặc bằng 4 MiB;
   không đóng gói video, weights hoặc embeddings. Summary ghi M8 pending,
   `demo_only=true`, `operational_ready=false`.

## Thiết kế và giới hạn

Backend gọi lại 12 CLI stage hiện có, không thay schema hoặc guard bảo vệ
dữ liệu. Dataset/job dùng JSON; một worker local tuần tự giải phóng CLIP
phục vụ trước khi index/tracking dùng GPU. CLIP FP16 batch 4, tracking theo
segment. Generated files nằm trong `outputs/ui-workflow/<dataset>/run-*/`;
cấu hình trong `run-*/configs`, tách khỏi thư mục output của các stage.

Upload tạo bản sao riêng; demo có sẵn tham chiếu `Demo_Data` chỉ đọc.
Mutation yêu cầu same-origin; giới hạn JSON 64 KiB, filename và đường dẫn
được kiểm tra. Review dùng revision guard; proposal dùng solver và journal
guard hiện có. Team, vùng và travel times đều giả định. Ngưỡng traffic dùng
baseline demo, chưa được calibration.

Khi restart, job dang dở thành interrupted; chọn dataset đã lưu để retry
hoặc mở kết quả. Không tự chạy pipeline khi mở trang. Không hỗ trợ nhiều
server/worker cùng ghi workspace; launcher dùng một uvicorn worker.
Không bổ sung OCR/ASR hay benchmark chất lượng khi chưa có nhãn độc lập.

## Kiểm chứng trên Windows

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Test-Windows.ps1
node --check .\static\cctv\workflow.js
node --check .\static\cctv\events.js
git diff --check
```

Smoke thực 2026-10-01: video H.264 tổng hợp 12 giây upload qua HTTP local;
UI lưu ROI/10 giây tracking, chạy 12/12 stage, tạo 24 keyframes, index CLIP
768D, 250 frame tracking, 5 traffic windows và MP4 overlay. Text query trả
12 kết quả; browser mở video seek giây 8, duration 12, readyState 4.
Review needs_more_evidence revision 1 cho case tổng hợp; proposal 0 assignment,
1 unmet theo review gate. ZIP được tải qua nút UI. Upload/file bounds,
failure/retry cùng run và shutdown không reload model có unittest.
Kiểm thử hồi quy: **264 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt;
Ruff và kiểm tra cú pháp JavaScript đạt. PowerShell đọc launcher từ disk bằng
parser Windows PowerShell để bắt lỗi encoding/terminator.

Browser automation không kiểm thử hoàn tất native file chooser vì extension
Chrome chưa có quyền file URL; upload API và backend đã kiểm thử bằng file thật.
Không thay quyền browser tự động. Người dùng mở UI bằng Chrome thông thường
không cần extension để chọn file.

```powershell
git add app/cctv.py app/cctv_workflow.py static/cctv scripts/Open-CctvDemo.ps1 scripts/Test-Windows.ps1 Open-CctvDemo.cmd test_cctv_workflow.py test_powershell_scripts.py README.md docs/CCTV_UI_WORKFLOW.md docs/CCTV_PROGRESS.md
git commit -m 'feat: process CCTV demos end to end through the UI'
git push origin main
```
