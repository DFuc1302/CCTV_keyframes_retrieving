# T6.2: benchmark retrieval/runtime trên Windows

## Khái niệm và thiết kế

Đo thời gian client chờ **toàn bộ HTTP response** của API retrieval hiện có.
Khoảng đo gồm upload ảnh, preprocessing, CLIP, FAISS, SQLite, serialize và
transfer JSON; không gồm đọc ảnh từ đĩa, kiểm tra JSON phía client, cold start
hoặc warmup. Không suy ra riêng thời gian GPU hoặc tốc độ toàn pipeline video.

Client chạy CPU, không khởi tạo CLIP thứ hai. Server dùng một CLIP FP16 trên
CUDA và khóa tuần tự text/image inference. Workload cố định trong
`configs/cctv.runtime-benchmark.demo.json`: 6 câu tiếng Anh và 6 ảnh đã index
(mỗi video một ảnh), top-k=12, warmup=2/query, đo 10 lần/query. Tổng 24 warmup
và 120 request đo, concurrency=1, thứ tự case cố định. Ảnh query lấy từ chính
index phục vụ kiểm thử đường image query; không dùng làm nhãn relevance.

P50/P95 là quantile thực nghiệm với nội suy tuyến tính tại `(n-1)*p` trong
mẫu đã sắp xếp; chỉ các request thành công được đưa vào latency. Bảng luôn
kèm attempted/failed và số lỗi warmup. Mẫu nhỏ, tải tuần tự, không phải cam kết
SLA; không chặn client khác, nhiệt độ GPU/power mode và thứ tự query có thể
ảnh hưởng kết quả. Warmup riêng từng case không loại bỏ mọi ảnh hưởng cache.

`/ready` bổ sung `benchmark_identity`: session UUID mới mỗi startup, checksum
index publication, recipe model, checksum code retrieval, Python/package/GPU.
Client đối chiếu với index/model/code cục bộ trước và sau mỗi case. Metadata
này là provenance cho demo tin cậy trên localhost, không phải remote attestation.
`model_memory` ghi peak allocator trong **toàn phiên model**, không phải tổng
VRAM hệ thống hoặc peak riêng từng request. Không reset allocator phục vụ demo.

## Code và output

Code đầy đủ: `pipeline/cctv/benchmark_runtime.py`; contracts workload, bộ đo,
validator ID/locator/cosine order, thống kê, checkpoint và CLI nằm trong module.
Test CPU với transport giả: `test_cctv_runtime_benchmark.py`; readiness thật
được kiểm thử bởi `test_cctv_api.py`. Không sửa schema/index legacy.

Input được đóng băng bằng SHA256: source manifest, workload, index state và
4 artifacts index, 6 ảnh query, code server/CLIP/search và module benchmark.
Raw chỉ đọc; output không được chồng lên raw, keyframes, index hoặc input.
Client chỉ upload processed JPEG đã đăng ký tới HTTP loopback có port rõ ràng;
không follow redirect hoặc dùng HTTP proxy của môi trường.

`runtime_state.json` checkpoint sau mỗi query, lưu samples/results/lỗi và
checksum case ledger. Query đang dở được đo lại sau warmup; các query hoàn
tất được giữ. Resume dở chỉ cho phép cùng session server; nếu server restart,
dùng output mới. Sau complete, resume kiểm tra input/report hash và trả report
cũ, không gửi request hoặc sửa bytes/mtime. Đây là đọc lại phép đo đã lưu,
không phải đo mới. Hash phát hiện sửa vô tình, không thay thế chữ ký bảo mật.

`runtime_report.json` ghi config/input hashes, environment, sample RTT và ranked
locators, P50/P95/mean theo query và loại, lỗi kể cả warmup. Không có Recall@K,
MRR, accuracy vì chưa có nhãn độc lập; `semantic_retrieval_metrics=null`.
`FAILED_REQUESTS` trả CLI exit 1 dù đã xuất report lỗi để kiểm tra.

## PowerShell chạy và kiểm thử

Terminal thứ nhất, sau khi dừng phiên demo cũ bằng Ctrl+C:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Start-CctvDemo.ps1
```

Terminal thứ hai (giữ demo chạy, không chạy detector/model khác đồng thời):

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\.venv\Scripts\python.exe -m pipeline.cctv.benchmark_runtime `
  --manifest configs\cctv.demo.json `
  --workload configs\cctv.runtime-benchmark.demo.json `
  --index-dir outputs\demo-data\search-index `
  --output outputs\demo-data\runtime-benchmark-v1 `
  --url http://127.0.0.1:8080
```

Muốn đo lại, thay output bằng tên mới, chẳng hạn `runtime-benchmark-v2`.
Server thiếu identity trả hướng dẫn restart trước khi bắt đầu query.
Kiểm thử core chạy ở terminal riêng sau khi dừng demo để tránh model cùng GPU:

```powershell
.\scripts\Test-Windows.ps1
```

## Git

Chỉ push code/config/docs/tests; báo cáo, ảnh, video và model trong outputs
được gitignore. Sau kiểm thử:

```powershell
git add app/cctv.py pipeline/cctv/benchmark_runtime.py `
  configs/cctv.runtime-benchmark.demo.json test_cctv_runtime_benchmark.py `
  test_cctv_api.py scripts/Test-Windows.ps1 docs/CCTV_RUNTIME_BENCHMARK.md `
  docs/CCTV_PROGRESS.md README_CCTV.md
git commit -m 'feat: add frozen CCTV runtime retrieval benchmark'
git push origin main
```

## Kết quả cục bộ ngày 2026-10-01

Windows 11, RTX 4070 Laptop, PyTorch 2.7.1+cu126, CUDA build 12.6,
CLIP FP16 pinned revision `32bd64288804d66eefd0ccbe215aa642df71cc41`,
FAISS 768D/7.232 frame. 24 warmup và 120 request đo đều hợp lệ, lỗi=0:

| Query | Mẫu đo | P50 (ms) | P95 (ms) | Mean (ms) |
| --- | ---: | ---: | ---: | ---: |
| Text | 60 | 52,66 | 56,43 | 52,94 |
| Image | 60 | 127,91 | 141,34 | 129,30 |

Model-lifetime allocator peak: allocated=843,79 MiB, reserved=862 MiB.
Core suite: 182 tests đạt; CUDA FP16/NMS và dependency check đạt. Resume giữ
hash/mtime của 2 report/state, không gửi thêm HTTP; raw 6 file và upstream
artifacts không đổi. Báo cáo đầy đủ cục bộ:
`outputs/demo-data/runtime-benchmark-v1/runtime_report.json`; kiểm chứng:
`outputs/demo-data-review/runtime-benchmark-verification.json`.

T6.2 hoàn tất công cụ và phép đo kỹ thuật cục bộ; chất lượng retrieval/event
vẫn cần nhãn độc lập. T6.3 tiếp nối bằng gói bàn giao/báo cáo và checklist máy
thứ hai; không được ghi đã nghiệm thu máy thứ hai khi chưa thực sự chạy.
