# Kế hoạch và thiết kế: Hai tùy chọn xử lý video (Trên thiết bị vs Google Colab)

## 1. Hiện trạng triển khai

Hệ thống đã triển khai đầy đủ cả hai cơ chế xử lý video trong mã nguồn:

1. **Xử lý trên thiết bị (On-Device Local Pipeline)**:
   - Backend: `app/cctv_workflow.py` (`workflow.start(...)`, endpoint `POST /cctv/workflow/{dataset_id}/jobs`).
   - Xử lý tuần tự 12 công đoạn (ingestion, CLIP 768D, FAISS, YOLOv8 detection, ByteTrack, track stitching, traffic windows, queue-context incidents, overlay rendering, dispatch proposal).
   - Sử dụng GPU NVIDIA cục bộ (RTX 4070 Laptop) hoặc CPU với cơ chế process isolation (`ProcessClipEncoder`) chống nghẽn commit.

2. **Xử lý trên Google Colab (Cloud T4 GPU Pipeline)**:
   - Backend: `pipeline/cctv/cloud.py` (`export_job`, `run_job`, `unpack`, `pack`, `import_result`).
   - Endpoint:
     - `GET /cctv/workflow/{dataset_id}/colab/job`: Đóng gói video đã chọn, cấu hình ROI, camera, mã nguồn và git commit thành gói ZIP portable.
     - `POST /cctv/workflow/{dataset_id}/colab/result`: Nhận gói kết quả ZIP từ Colab, kiểm tra toàn vẹn SHA256 và rebind đường dẫn, kích hoạt run vào UI mà không tiêu tốn tài nguyên cục bộ.
   - Notebook: `notebooks/CCTV_Colab.ipynb` hỗ trợ chạy tự động trên Tesla T4 GPU với cơ chế snapshot checkpoint lên Google Drive.

## 2. Nút thắt cần cải tiến (UX / UI Gap)

Tại giao diện Web (`static/cctv/index.html` và `static/cctv/workflow.js`), khu vực xử lý (Mục 3: "Chạy và xem kết quả") hiện đang gộp tất cả các nút bấm trên cùng một hàng khiến người dùng chưa thấy rõ **hai tùy chọn độc lập**:
- Nút "Chạy toàn bộ pipeline" nằm lẫn với "Xuất job Colab" và "Nhập kết quả Colab".
- Chưa có bộ chọn chuyển đổi rõ ràng giữa chế độ **Xử lý trên thiết bị cục bộ** và **Xử lý qua Google Colab**.

## 3. Giải pháp nâng cấp giao diện

1. **Bổ sung bộ chọn 2 chế độ (Mode Selector)**:
   - Thêm tab hoặc radio switch:
     - `[•] Chạy trên thiết bị (On-Device)`: Dành cho máy có GPU rời (NVIDIA RTX) hoặc muốn xử lý trực tiếp cục bộ.
     - `[ ] Chạy trên Google Colab (Cloud GPU)`: Dành cho máy cấu hình nhẹ, tận dụng GPU Tesla T4 miễn phí trên cloud.

2. **Giao diện phân nhánh theo chế độ được chọn**:
   - Khi chọn **Chạy trên thiết bị (On-Device)**:
     - Hiển thị nút "Chạy toàn bộ pipeline", "Thử lại", "Dừng", thanh tiến độ xử lý và trạng thái job.
     - Ẩn khối thao tác Colab để tránh gây rối mắt.
   - Khi chọn **Chạy trên Google Colab**:
     - Hiển thị bảng hướng dẫn 3 bước trực quan:
       - **Bước 1**: Nhấn "Xuất job Colab (.zip)" để tải gói dữ liệu và mã nguồn.
       - **Bước 2**: Nhấn "Mở notebook Colab" để mở Colab, tải gói zip lên Drive/Colab và chạy tự động trên GPU Cloud.
       - **Bước 3**: Nhấn "Nhập kết quả Colab (.zip)" để nạp file kết quả về máy và mở các tab tra cứu, giao thông, sự kiện.
     - Ẩn nút chạy local.

3. **Bảo toàn tính tương thích**:
   - Giữ nguyên toàn bộ ID phần tử HTML (`workflow-run`, `workflow-stop`, `workflow-colab-export`, `workflow-colab-import`, `workflow-activate`, v.v.) để bảo đảm các bài kiểm thử JavaScript và Python hoạt động ổn định 100%.

## 4. Các bước thực hiện

1. [x] Viết kế hoạch chi tiết `docs/CCTV_EXECUTION_OPTIONS_PLAN.md`.
2. [ ] Cập nhật `static/cctv/index.html` với bộ chọn chế độ xử lý trực quan.
3. [ ] Cập nhật `static/cctv/workflow.js` xử lý sự kiện chuyển đổi giữa hai chế độ.
4. [ ] Cập nhật tài liệu hướng dẫn `docs/CCTV_UI_WORKFLOW.md`.
5. [ ] Kiểm tra cú pháp JS (`node --check`) và chạy bộ kiểm thử Windows (`scripts/Test-Windows.ps1`).
