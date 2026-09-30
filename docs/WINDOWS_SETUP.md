# T0.2 — môi trường Windows Native

Mục tiêu: Python 3.11 x64 trong `.venv`, PyTorch CUDA cho RTX 4070 Laptop 8GB, dependency retrieval và vision. Chưa có CCTV hoặc serving bundle; kiểm tra runtime không chứng minh độ chính xác tìm kiếm hoặc phát hiện sự kiện.

## Thiết kế

- Giữ requirements WSL cũ làm tài liệu kế thừa. Dùng các file `windows-*.txt` cho Windows.
- Torch 2.7.1 và torchvision 0.22.1 từ index CUDA 12.6 chính thức; FAISS chạy CPU.
- NumPy 1.26.4 giữ ràng buộc `numpy<2` của nguồn. OpenCV 4.11.0.86 dùng một distribution `opencv-python`, tránh cài thêm headless song song.
- `windows-runtime.txt`: phục vụ và retrieval; `windows-dev.txt`: thêm HTTP client cho kiểm thử; `windows-vision.txt`: thêm YOLO.
- `windows-py311-cu126.lock.txt` là snapshot phiên bản đã cài và kiểm thử, bao gồm vision. Setup ưu tiên lock khi file tồn tại. Chỉ dành cho CPython 3.11 x64 Windows với CUDA 12.6.
- OCR/Whisper chưa cài và chưa tải model. Nhánh xử lý này tùy chọn cho CCTV; thêm dependency riêng khi cần, kiểm tra lại xung đột OpenCV và đóng băng môi trường mới.

Nguồn đối chiếu: [cặp torch/torchvision CUDA Windows](https://pytorch.org/get-started/previous-versions/), [FAISS wheels](https://pypi.org/project/faiss-cpu/), [OpenCV 4.11](https://pypi.org/project/opencv-python/4.11.0.86/).

## Cài đặt và chạy kiểm tra

Đã có Python 3.11 x64, NVIDIA driver, FFmpeg và ffprobe trong PATH. Script không yêu cầu kích hoạt `.venv`, không thay đổi execution policy và không cài vào Python global.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Setup-Windows.ps1 -Vision
.\scripts\Test-Preflight.ps1
.\scripts\Test-Windows.ps1
```

Chạy bộ legacy đầy đủ để xem các lỗi kế thừa đã ghi nhận:

```powershell
.\scripts\Test-Windows.ps1 -FullLegacy
```

Mặc định script chạy kiểm tra runtime, 10 test độc lập cho UI/assets, bộ lọc video, tiền xử lý OCR và map timestamp, cùng 16 test CCTV manifest/ingestion bổ sung ở M1. `-FullLegacy` chạy discovery toàn bộ và trả lỗi nếu có test thất bại; không bỏ qua hoặc đổi nhãn các lỗi này.

`Setup-Windows.ps1` tạo `.venv` nếu thiếu, dừng ngay khi lệnh lỗi và kiểm tra CUDA sau cài đặt. Có thể chạy lại để tiếp tục một lần cài bị gián đoạn. Lần đầu cần mạng để tải wheel; không tải trọng số CLIP/YOLO.

Kiểm tra GPU trực tiếp:

```powershell
.\.venv\Scripts\python.exe .\scripts\verify_runtime.py --require-cuda
.\.venv\Scripts\python.exe -m pip check
```

Kiểm tra thực hiện JPEG encode/decode, FAISS có ánh xạ ID, SQLite FTS5, import giao diện CLIP, phép toán FP16 và NMS trên CUDA. `torch.amp.autocast('cuda', dtype=torch.float16)` là API AMP hiện hành; sau phép kiểm tra giải phóng tensor, gọi garbage collection và `torch.cuda.empty_cache()`.

## Đầu ra và giới hạn

- `outputs/preflight/environment.json`: package, thiết bị và đường dẫn công cụ.
- `outputs/preflight/runtime-checks.json`: kết quả kiểm tra thực thi.
- `outputs/tests/legacy-tests.txt`: bộ test legacy không cần dataset thật.
- `outputs/tests/core-tests.txt`: bộ runtime/CCTV Windows; tách log khỏi bộ discovery.
- Các paths của kiểm thử được đặt riêng dưới `outputs/tests`; môi trường được khôi phục sau khi chạy. Không dùng cấu hình writable của test để phục vụ CCTV thật.
- Khi viết pipeline inference, bắt đầu batch nhỏ (ví dụ 1–4), đo VRAM rồi điều chỉnh. Phép toán GPU tổng hợp không chứng minh model CLIP/YOLO thực tế sẽ vừa VRAM.
- T0.3 cần dataset mới hoặc bundle hợp lệ; không tự tạo dataset giả để tuyên bố baseline đã sẵn sàng.

## Kết quả xác minh ngày 2026-09-30

- Python `.venv`: 3.11.9; torch 2.7.1+cu126; torchvision 0.22.1+cu126.
- CUDA 12.6 hoạt động trên RTX 4070 Laptop; FP16 matmul và CPU/CUDA NMS đạt. Peak tensor allocation của phép kiểm tra nhỏ: 10 MiB, không phải nhu cầu VRAM của model thực.
- NumPy 1.26.4; OpenCV 4.11.0.86; FAISS CPU 1.15.1.
- `pip check`, Python compile và kiểm tra syntax PowerShell đạt.
- JPEG, FAISS ID mapping, SQLite FTS5 và import giao diện CLIP/SentenceTransformer đạt, không tải trọng số.
- Import YOLO/ImageHash và CLI `--help` của ASR/new-dataset đạt, không tải trọng số.
- Chạy lại setup theo lockfile thành công; preflight tự chọn Python trong `.venv`.
- Kiểm tra 8 wrapper root/package cùng module identity và truy cập private runtime state đạt.
- Core tests: 10 test đạt.
- Full discovery: 37 test; báo `failures=2, errors=13, skipped=2`. Một số test có lỗi ở cả thực thi và cleanup, nên không suy số test đạt bằng cách trừ đơn giản.

Đã sửa lỗi cản import backend: wrapper root giờ dùng cùng module với package, giữ private attributes và trạng thái; telemetry đọc đúng file triển khai ở cấu trúc package. Test UI kiểm tra mọi tài nguyên được HTML tham chiếu và chấp nhận MIME JavaScript hợp lệ trên Windows thay vì cố định số lượng hoặc đòi `text/`.

## Các lỗi legacy còn tồn tại, ngoài nghiệm thu môi trường

| Nhánh | Vấn đề thấy trong full discovery | Việc cần làm riêng |
| --- | --- | --- |
| Dedup | Tạo symlink cần đặc quyền Windows; SQLite connection bị giữ khi cleanup | Thiết kế fallback không cần admin, đóng connection rõ ràng |
| Partial serving | Fixture SQLite không đóng; thiếu facets; kết quả Video Match khác kỳ vọng; wrapper merge không xuất helper private | Chuẩn hóa fixture/connection và hợp đồng partial serving |
| Redis migration | Test đòi `FrameRows`/text-only import nhưng implementation chưa có | Khôi phục hợp đồng migration hoặc cập nhật thiết kế có kiểm thử |
| ASR normalization | Trường tìm kiếm chưa normalize như test yêu cầu | Chuẩn hóa search field, giữ transcript gốc |
| Regression archived tool | Import `import_colab_batch2` ở root không còn sau refactor | Cập nhật import hoặc bổ sung wrapper tương thích |
| Summary | Đường dẫn index cũ, kết quả combined không khớp fixture | Chuẩn hóa cấu hình summary và kiểm thử kết hợp |

Hai test skipped cần Bash hoặc Redis Stack riêng. T0.2 xác minh runtime Windows; chưa chứng minh mọi chức năng legacy hoạt động và chưa nghiệm thu retrieval với CCTV thật.

## Git workflow

Các thay đổi tương thích có commit riêng: `fix: restore legacy runtime module compatibility`.
Môi trường và tài liệu T0.2: `build: add verified native Windows CUDA environment`.

Sau chỉnh sửa runtime, chạy core checks, review diff và chỉ stage mã/tài liệu:

```powershell
.\scripts\Test-Windows.ps1
git diff --check
git diff
git add -- scripts requirements docs README_CCTV.md
git diff --cached --check
git diff --cached --stat
git commit -m "build: add verified native Windows CUDA environment"
git push origin main
```
