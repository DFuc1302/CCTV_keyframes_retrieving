# CCTV keyframe retrieval — VGU Explore Computer Science

Project kế thừa hệ thống tìm kiếm video đa phương thức, mở rộng sang truy hồi CCTV, phát hiện nghi tai nạn/tắc đường và hỗ trợ phân bổ cảnh sát. Môi trường mục tiêu: Windows 11 Native, PowerShell, RTX 4070 Laptop 8GB.

Đọc `docs/M0_INVENTORY.md` để biết nguồn kế thừa, môi trường đã khảo sát và các điều kiện còn thiếu. `README.md` là tài liệu legacy, chứa hướng dẫn WSL và không đại diện cho quy trình Windows mới.

Hướng dẫn runtime Windows: `docs/WINDOWS_SETUP.md`.

Pipeline CCTV mới: `docs/CCTV_INGESTION.md`, dùng manifest độc lập và giữ map/keyframe legacy. Có demo tổng hợp khi chưa có dữ liệu thật.

Dataset demo đã đăng ký: `D:\Project ExploreCS\Demo_Data`, gồm 6 video từ hai camera N001/N002. Manifest: `configs/cctv.demo.json`. Xem `docs/DEMO_DATA.md` để chạy extraction và biết giới hạn đánh giá.

CLIP/FAISS/SQLite và CLI tìm kiếm text/image: `docs/CCTV_RETRIEVAL.md`. Pipeline giữ ID/schema legacy, dùng FP16 trên CUDA và checkpoint theo batch.

Demo FastAPI tìm kiếm text/image và mở video tại timestamp: `docs/CCTV_DEMO_UI.md`.
Sau khi chuẩn bị MP4 previews, chạy `.\scripts\Start-CctvDemo.ps1` và mở
`http://127.0.0.1:8080`. API dùng một CLIP instance, đọc index hiện có và không sửa video gốc.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Setup-Windows.ps1 -Vision
.\scripts\Test-Preflight.ps1
.\scripts\Test-Windows.ps1
```

Raw CCTV và dữ liệu kế thừa luôn chỉ đọc. Dữ liệu, model, index và báo cáo máy cục bộ không đưa vào Git. Chỉ mục mới và log nằm trong output riêng; kiểm kê không tải model hoặc tự động xây index.
