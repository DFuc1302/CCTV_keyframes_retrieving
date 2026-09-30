# M0: tiếp nhận nền CCTV trên Windows

Ngày khảo sát: 2026-09-30. Đây là kiểm kê, không phải kết quả nghiệm thu retrieval.

## Nguồn và cách tiếp nhận

- Nguồn kế thừa: `D:\AIC_26\CODE`.
- Commit nguồn: `5c889d7cbbc03add8f2f23b19300ee73af96cae2`.
- Repo đích: `https://github.com/DFuc1302/CCTV_keyframes_retrieving.git`, branch `main`.
- Sao chép 219 file được Git theo dõi, giữ cấu trúc package và wrapper cũ.
- `.gitignore` đích được giữ và bổ sung quy tắc loại dữ liệu/model/đầu ra.
- README nguồn có sửa đổi chưa commit tại thời điểm tiếp nhận; bản sao là nội dung working tree, không phải snapshot sạch tuyệt đối của commit trên.
- Tài liệu định hướng của người dùng được sao chép vào `docs/KE_THUA_VA_PHAT_TRIEN_DO_AN.md`.
- Không sao chép `.git`, database runtime, video, model, vector hoặc file không được Git theo dõi từ nguồn.
- Không sửa mã hoặc dữ liệu trong thư mục nguồn. Chưa thấy LICENSE cấp gốc; chưa xác minh tác giả hoặc điều kiện tái phân phối.
- Repo kế thừa do người dùng cung cấp: `https://github.com/DFuc1302/multimodal-video-search-platform`, HEAD `040eeb0ddcd471978bd159dd3efa2ebd63cbba3c` (Archive AIC code as reusable project baseline).
- Bản GitHub có bố cục phẳng cũ; bản tại `D:\AIC_26\CODE` có package `app/`, `pipeline/` đúng tài liệu kế thừa. Giữ bản local làm nền theo tài liệu, không chép đè bằng bản GitHub cũ. Bản remote được tải vào `outputs/upstream-review` để đối chiếu, không đưa vào commit.

## Thành phần được tiếp nhận

`app/`, `pipeline/`, `static/`, giao diện HTML, benchmark, tests và wrapper ở root được giữ nguyên.
Các launcher WSL là di sản được lưu để đối chiếu, không phải hướng dẫn chạy của project Windows.

- Frame extraction: FFmpeg, khoảng 2 FPS; CSV `n,pts_time,fps,frame_idx`.
- Retrieval: CLIP chuẩn hóa vector; FAISS FlatIP/HNSW với ánh xạ ID.
- Metadata: SQLite; OCR và detections, Redis tùy chọn.
- YOLO, OCR và Whisper đã có đường tích hợp nhưng chưa chạy trên môi trường mới.
- Chưa có module CCTV tracking, sự kiện giao thông hoặc phân bổ cảnh sát được nghiệm thu.

## Môi trường phát hiện ban đầu

- Windows Native, PowerShell; Python 3.11.9.
- GPU: NVIDIA GeForce RTX 4070 Laptop GPU, 8188 MiB; driver 580.97.
- Có Git, FFmpeg, ffprobe và nvidia-smi trong PATH.
- Python hiện tại có NumPy 2.4.6, tqdm 4.70.0; thiếu các dependency chính như torch, FAISS, OpenCV, transformers, FastAPI và uvicorn.
- Requirements kế thừa yêu cầu `numpy<2.0`; cần môi trường riêng, không thay đổi Python global.
- CUDA trong PyTorch chưa kiểm chứng; nvidia-smi không chứng minh torch chạy được CUDA.
- Chưa xác định bundle, keyframe/map, model và CCTV đầu vào hợp lệ. Không khởi động ứng dụng với cấu hình mặc định vì có thể tạo đầu ra ngoài ý muốn.
- README của repo GitHub yêu cầu tải riêng `DATA_FOR_TEAM` và `filtered_data_all_873_moved` từ Drive. Danh sách file Git không có `.db`, `.faiss` hoặc `.mp4`; repo này chưa cung cấp serving bundle để chạy tìm kiếm.

## Kết quả kiểm tra thực tế trong lần tiếp nhận

- `python -m compileall -q app pipeline scripts`: đạt kiểm tra cú pháp.
- Preflight Python và launcher PowerShell: chạy thành công, báo dependency thiếu; không xác nhận readiness.
- `python -m unittest discover -v`: 23 test, 15 đạt, 8 lỗi import do thiếu `faiss`, `fastapi`, `redis`. Log đầy đủ ở `outputs/preflight/legacy-tests.txt`.
- `git diff --check`: đạt trước khi commit.
- T0.1 hoàn thành kiểm kê mã/môi trường và xác định phần thiếu. T0.2 mới có công cụ preflight; chưa cài runtime. T0.3 chưa chạy vì thiếu bundle và dependency.

## Chạy lại kiểm kê

Từ thư mục gốc repo trong PowerShell:

```powershell
python .\scripts\preflight_windows.py --save
.\scripts\Test-Preflight.ps1
```

Nếu có bundle, thêm `--data-root` với đường dẫn thực tế. Script chỉ kiểm tra sự tồn tại, không mở database ghi hoặc tải model. Báo cáo nằm ở `outputs/preflight/environment.json`, được Git bỏ qua.

## Các bước tiếp theo và điều kiện hoàn thành

1. T0.2: tạo `.venv`, xác minh dependency Windows và torch CUDA; tách requirements phục vụ/pipeline sau kiểm thử. Chưa dùng launcher WSL hoặc cài dependency vào Python global.
2. T0.3: có bundle và model khớp manifest; chạy API với `SERVE_READ_ONLY=1`, `REQUIRE_SERVE_BUNDLE=1`, `AUTO_INDEX_ON_STARTUP=0`, `ALLOW_HEAVY_RUNTIME_JOBS=0`, `OCR_ON_DEMAND=0`.
3. Kiểm tra `/ready` trả 200, truy vấn đối chứng, ảnh và timestamp. Không lấy `/health` hoặc kiểm tra cú pháp làm bằng chứng retrieval hoạt động.
4. Khi không có bundle: xử lý tập CCTV nhỏ vào output riêng ở milestone tiếp theo, không giả lập readiness.

Thông số batch và FP16 sẽ được đo khi chạy model. Không đổi model embedding của bundle chỉ để giảm VRAM.

## Cập nhật T0.2

Người dùng xác nhận chưa tạo hoặc lưu dữ liệu CCTV. Đã tạo `.venv` Windows và cài runtime/vision, có lockfile theo Python 3.11/CUDA 12.6; Python global giữ nguyên. GPU FP16, FAISS/OpenCV và 10 core tests đạt. Full legacy suite vẫn còn lỗi đã ghi chi tiết trong `docs/WINDOWS_SETUP.md`; T0.3 chưa thể chạy khi chưa có dataset hoặc bundle.
