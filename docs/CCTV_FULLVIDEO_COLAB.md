# Toàn video, tốc độ và Colab batch

## Phạm vi

UI mặc định tracking **toàn video**, lấy frame theo PTS mỗi **0,5 giây**.
Chế độ **khoảng** vẫn giữ start/duration 1–60 giây; tìm kiếm/keyframe/index
luôn dùng toàn video. **Mọi frame** là lựa chọn khi cần tracking dày hơn.
2 FPS giữ timestamp và ordinal của frame gốc, không giả định video có FPS cố định.
Overlay 2 FPS có thể giật, bỏ lỡ chuyển động nhanh; ID và rules chưa hiệu chỉnh
cho dữ liệu thật. ROI và M8 vẫn pending, dispatch chỉ mô phỏng.

## Thiết kế và tối ưu

- Decode bằng generator, chọn frame theo PTS, inference YOLO tối đa 4 ảnh/batch.
  CUDA dùng FP16; OOM giảm batch 4→2→1, chưa commit segment lỗi.
- ByteTrack cập nhật theo thứ tự; track buffer điều chỉnh theo tần suất lấy mẫu.
  ID được reset mỗi segment 5 giây như kiến trúc sẵn có.
- Traffic/incidents nhận biết ordinal thưa; vẫn kiểm tra PTS, gaps và nguồn.
  Không tự suy ra tai nạn từ mất ID hay bỏ frame.
- Run mới cùng video/camera/model/code có thể dùng lại inventory, keyframes,
  CLIP index và preview đã xác minh checksum. Bản sao đổi đường dẫn/hash phụ
  thuộc để run mới tự chứa artifacts; raw và run cũ giữ nguyên.
- Mỗi stage có timing trong `run/stage-timings.jsonl`, gồm startup subprocess.
  Có thể tải timing qua nút **Tải báo cáo ZIP** trong UI.
  Decode khoảng xa vẫn đọc từ đầu để giữ ordinal/PTS; đây có thể là bottleneck
  còn lại trên video dài. Chỉ thay decoder khi benchmark xác nhận và tests PTS đạt.

## Dùng qua UI Windows

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Start-CctvDemo.ps1 -Port 8080
```

1. Chọn/tải video, gán camera, kéo các đỉnh ROI, chọn toàn video/khoảng và chế độ.
2. Lưu camera/ROI, chạy pipeline local; hoặc **Xuất job Colab**.
3. **Mở notebook Colab**, chọn GPU, đăng nhập tài khoản của bạn và mount Drive.
4. Đưa job ZIP vào `MyDrive/CCTV_Colab`, sửa `job_name`, chạy các cell tuần tự.
5. Tải result ZIP → chọn lại đúng dataset local → **Nhập kết quả Colab**.
   Bộ kiểm tra xác minh nguồn/camera/ROI/code, chuyển đường dẫn Linux sang Windows,
   reseal bản sao mới, kiểm tra retrieval/traffic/overlay rồi kích hoạt.

ZIP tối đa 4 GiB, giải nén tối đa 20 GiB và 100.000 entries. Bắt đầu với một
video ngắn. ZIP checksum phát hiện thay đổi, không phải chữ ký xác thực nhà gửi.
Không sửa hoặc nhập result của code/ROI/video khác; hãy xuất job mới.
Job ZIP có bản sao video demo, không có weights, tài khoản hay credentials.
Result không chứa raw video; nguồn local phải còn nguyên.

## Resume và chi phí

Colab chạy batch qua notebook; UI phục vụ trên Windows. Model cache nằm trên
Drive, sao chép vào ổ Colab trước khi xử lý. Checkpoint ZIP sau mỗi stage và
SQLite backup sau mỗi segment đã commit; cùng job, checkout và môi trường
có thể tiếp tục sau runtime loss. Segment chưa commit sẽ được xử lý lại.
Whole-DB backup mỗi segment có trần hiệu năng: nếu Drive I/O chiếm nhiều thời
gian, mới cân nhắc giảm tần suất hoặc backup tăng dần. Không cam kết GPU/uptime.

Theo [Google One](https://support.google.com/googleone/answer/14534406?hl=en),
Google AI Pro có quyền lợi 200 CCUs, với điều kiện tài khoản/tuổi/chủ gói và
không áp dụng trial. Bạn phải xác minh balance thực tế; notebook không mua
compute, không tạo GCP VM và không gọi API tính phí. Nếu hết quota, dùng local.
[Colab FAQ](https://research.google.com/colaboratory/faq.html) nêu tài nguyên
không được đảm bảo và runtime có thể bị ngắt. Khi xong, disconnect/delete runtime.

## Kiểm thử và bằng chứng

```powershell
.\scripts\Test-Windows.ps1
.\.venv\Scripts\python.exe -m unittest -q test_cctv_cloud test_cctv_fullvideo
node --check static/cctv/workflow.js
node scripts/test_cctv_roi.cjs
```

Đã đo GPU local RTX 4070 Laptop, video test-pattern 12 giây, tracking khoảng
10 giây: baseline native batch 1 xử lý 250 frames mất **12,48 s**;
sampled batch 4 xử lý 20 frames mất **8,31 s**, gồm startup/model (~33% ít thời
gian hơn trong lần đo này). Hai chế độ không tương đương về chất lượng.
Không coi đây là benchmark CCTV thật hoặc dự báo tốc độ Colab.
Logs: `outputs/tests/fullvideo-baseline*`, `fullvideo-fast-v2*`.

Regression có video giả lập 64 giây để kiểm tra full tracking/overlay >60 giây,
PTS, resume và đổi recipe. Bộ kiểm thử cloud kiểm tra ZIP traversal/tamper,
case collisions, Linux→Windows, dependency hashes/SQLite, source SHA/mtime,
khôi phục segment và stage sau runtime loss. Notebook chỉ đã kiểm tra cấu trúc
và cú pháp local; **Colab GPU execution/benchmark: NOT_RUN**.

Integration GPU local chạy pipeline qua API của UI trên video test-pattern
12 giây, kiểm tra 24 sampled frames đến PTS 11,52, search sau kích hoạt và
vòng export/import artifact thật. Đây là kiểm thử transport local, không phải
chạy Colab. Log/evidence: `outputs/tests/fullvideo-workflow-log-final.txt`,
`outputs/tests/fullvideo-workflow-evidence.json`.

Để hoàn thành so sánh Colab: chạy cùng job; giữ `cloud-environment.json`,
`cloud-runtime.json`, `stage-timings.jsonl`, đo upload/download/model setup
riêng. So sánh cùng video/scope/mode/cache state, báo cáo phần checkpoint I/O.
Chưa có số GPU cloud, chất lượng M8 hoặc máy thứ hai mới.

## Git

Các feature dùng Conventional Commits và đã/được push theo từng task.
Sau cập nhật từ repo trên máy khác:

```powershell
git pull --ff-only
.\scripts\Test-Windows.ps1
```
