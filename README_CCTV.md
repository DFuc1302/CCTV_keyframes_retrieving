# CCTV keyframe retrieval — VGU Explore Computer Science

Project kế thừa hệ thống tìm kiếm video đa phương thức, mở rộng sang truy hồi CCTV, phát hiện nghi tai nạn/tắc đường và hỗ trợ phân bổ cảnh sát. Môi trường mục tiêu: Windows 11 Native, PowerShell, RTX 4070 Laptop 8GB.

Đọc `docs/M0_INVENTORY.md` để biết nguồn kế thừa, môi trường đã khảo sát và các điều kiện còn thiếu. `README.md` là tài liệu legacy, chứa hướng dẫn WSL và không đại diện cho quy trình Windows mới.

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
python .\scripts\preflight_windows.py --save
```

Raw CCTV và dữ liệu kế thừa luôn chỉ đọc. Dữ liệu, model, index và báo cáo máy cục bộ không đưa vào Git. Chỉ mục mới và log nằm trong output riêng; kiểm kê không tải model hoặc tự động xây index.
