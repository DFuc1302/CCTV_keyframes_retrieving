# M2 — CLIP, FAISS và SQLite cho CCTV

## Concept và kiến trúc

Giữ `openai/clip-vit-large-patch14` của legacy, không trộn embedding với model khác.
Revision cố định: `32bd64288804d66eefd0ccbe215aa642df71cc41`; recipe còn hash file
weights, tokenizer và processor. Model dùng một instance, chỉ đọc cache cục bộ khi inference.
CUDA dùng FP16 và autocast; chuẩn hóa vector trong FP32 trước khi lưu float32.
Batch mặc định 4, giới hạn 1–16, chia đôi khi CUDA OOM; OOM ở batch 1 trả lỗi để resume.
Model được giải phóng và gọi garbage collection/empty_cache trước stage tiếp theo.

L2 normalization làm inner product tương đương cosine similarity.
`IndexIDMap2(IndexFlatIP(768))` tìm chính xác trên toàn bộ vector; với 7,232 ảnh, matrix
float32 chỉ khoảng 21.19 MiB, phù hợp baseline so với cấu hình HNSW cần thêm tham số.
Score là độ tương đồng, không phải xác suất tai nạn hoặc độ tin cậy tắc đường.
Model card mô tả image/text encoder và giới hạn ngôn ngữ: dùng query tiếng Anh cho
baseline; chưa khẳng định truy vấn tiếng Việt hoặc đếm xe hoạt động tốt.
Nguồn: [CLIP model card](https://huggingface.co/openai/clip-vit-large-patch14).

`pipeline/cctv/index.py` đọc dataset đã ingestion hoàn tất, đối chiếu hash/map/image
coverage trước indexing. Không sửa input. Dùng frame ID và int64 BLAKE2b của legacy,
giữ `frames` SQLite schema và sidecar `frames_meta.json` format 2.
Hai bảng bổ sung: `cctv_frames` (camera/video/time/frame_idx/source) và `cctv_vectors`
(embedding/checksum). Không tự suy timestamp tuyệt đối, không chạy OCR hoặc YOLO.

Mỗi batch commit metadata và vectors trong cùng một SQLite transaction. Gián đoạn
giữ batch đã commit; lần sau chỉ encode frame chưa có vector. Unreadable frame được
log riêng, xử lý tiếp frame khác; publication bị giữ lại khi còn frame lỗi.
Index được dựng từ vectors đã commit, serialize/đọc lại kiểm tra, rồi ghi checksum
publication. Search từ chối output chưa hoàn tất hoặc checksum không khớp.
Đổi input/model/recipe phải dùng output folder mới; không tự ghi đè input hoặc nhận
quyền sở hữu output đã có file nhưng chưa có checkpoint của pipeline.

`pipeline/cctv/search.py` mở database bằng SQLite URI `mode=ro`, hỗ trợ text-to-image,
image-to-image và kiểm tra stored vector. Kết quả gồm image_path, video nguồn,
camera_id, timestamp tương đối và decoded frame_idx. Chưa có multimodal fusion
hoặc nhận diện sự kiện. Demo FastAPI riêng đã kết nối text/image/video, xem
`CCTV_DEMO_UI.md`; composition/readiness legacy vẫn giữ nguyên.

### Truy vấn Việt–Anh theo miền CCTV

`app/query_hints.py` cung cấp `expand_bilingual_query`: từ vựng cố định gồm
19 cụm CCTV (tai nạn/va chạm, tắc đường/kẹt xe, các loại xe, giao lộ,
đường phố, ngày/đêm, mưa, cảnh sát, dừng lại, quay đầu). Matching dùng NFC,
casefold, ranh giới từ Unicode và ưu tiên cụm dài nhất; cụm đã match được
thay bằng mô tả tiếng Anh. Ví dụ `xe máy ban đêm` thành
`motorcycle motorbike night nighttime dark`; `tắc đường ngã tư` thành
`traffic jam congestion slow traffic road intersection crossroad`.

Đây là hỗ trợ từ vựng miền, không phải dịch máy tổng quát. Cụm không biết
vẫn còn trong prompt; khi có match, phần chưa dịch được chuẩn hóa NFC/casefold,
nên truy vấn hỗn hợp có thể vẫn chứa tiếng Việt. Không có match thì helper
giữ nguyên chính xác truy vấn, kể cả chữ hoa/khoảng trắng (HTTP/UI vẫn áp dụng
trim sẵn có). Không suy diễn ý nghĩa hoặc bổ sung từ cho cụm ngoài từ vựng.

CLI và API encode **chỉ prompt đã diễn giải**, không lấy trung bình với vector
truy vấn gốc và không nạp CLIP thứ hai. Với cùng encoder/index, truy vấn tiếng Việt
đã dịch và prompt tiếng Anh tương ứng có cùng thứ hạng. HTTP text search giữ
`results`/`total`, thêm `translated_prompt` (chuỗi khi có match, nếu không là
`null`) và `translated_terms` (các cụm Việt duy nhất theo thứ tự xuất hiện,
hoặc `[]`). UI hiển thị prompt này trong trạng thái kết quả bằng `textContent`;
CLI vẫn xuất JSON dạng danh sách kết quả cũ. Image search không đổi contract.
Thông báo nạp CLIP đi vào stderr; stdout của CLI search chỉ chứa JSON, để consumer
có thể parse trực tiếp mà không phải cắt bỏ các dòng startup.

Không sửa ảnh, stored vectors hoặc index cũ; không cần migration/model mới.
Các regression tests dùng index RGB synthetic có distractors để kiểm tra thứ hạng
và metadata, không đo recall đa ngôn ngữ thực tế. Chưa có relevance labels độc lập
hoặc đánh giá recall Việt–Anh trên video thật; không coi domain expansion là bằng
chứng CLIP hiểu tiếng Việt tổng quát.

## Chạy Windows

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'

# Tải model public một lần; token tài khoản không cần thiết
.\.venv\Scripts\python.exe -m scripts.download_cctv_clip
# Khi cache đã đầy đủ, dùng offline
.\.venv\Scripts\python.exe -m scripts.download_cctv_clip --offline

.\.venv\Scripts\python.exe scripts\run_python_logged.py --log outputs\demo-data-review\index-console.txt -- -u -m pipeline.cctv.index --input-dir outputs\demo-data\processed --output-dir outputs\demo-data\search-index --model-config outputs\model-cache\clip.json --device cuda --batch-size 4

# Chạy lại cùng lệnh để resume; hoàn tất thì skip và không nạp model
.\.venv\Scripts\python.exe scripts\run_python_logged.py --log outputs\demo-data-review\index-resume-console.txt -- -u -m pipeline.cctv.index --input-dir outputs\demo-data\processed --output-dir outputs\demo-data\search-index --model-config outputs\model-cache\clip.json --device cuda --batch-size 4

# Text retrieval
.\.venv\Scripts\python.exe -m pipeline.cctv.search --index-dir outputs\demo-data\search-index --model-config outputs\model-cache\clip.json --query 'heavy traffic at a road intersection at night' --top-k 5

# Image retrieval
.\.venv\Scripts\python.exe -m pipeline.cctv.search --index-dir outputs\demo-data\search-index --model-config outputs\model-cache\clip.json --image outputs\demo-data\processed\keyframes\N001-V001\001.jpg --top-k 5

# Kiểm tra FAISS/metadata, không nạp model
.\.venv\Scripts\python.exe -m pipeline.cctv.search --index-dir outputs\demo-data\search-index --stored-frame 'N001-V001/001.jpg' --top-k 5

# Chạy test sau khi indexing/inference đã kết thúc để giảm bộ nhớ đồng thời
.\scripts\Test-Windows.ps1
```

CLI index trả exit 1 khi lỗi, 130 khi bị Ctrl+C. Với logged runner, kiểm tra
`$LASTEXITCODE` và log. Không thay chính sách execution PowerShell hoặc kích hoạt
venv; gọi Python trong venv trực tiếp. Nếu không có CUDA có thể dùng `--device cpu`
với output mới; CUDA/CPU precision khác nhau nên không trộn checkpoint hai recipe.
Download dùng cache output, không cần bật Windows Developer Mode cho symlink.
Inference và search không truy cập mạng hoặc gửi ảnh ra ngoài.

## Output và phạm vi nghiệm thu

```text
outputs/demo-data/search-index/
  meta.db                         legacy frames + CCTV locators/vectors
  faiss_index/frames.faiss         exact cosine index
  faiss_index/frames_meta.json     legacy ID/file sidecar
  index_state.json                 ownership/recipe/publication checksum
  index_manifest.json              source asset roots/dim/count/VRAM
  logs/index.log
```

Keyframes/maps được tham chiếu tới extraction output, không sao chép hoặc sửa.
`retrieval_ready=true` là readiness cho CLI này; `serving_ready=false` thuộc serving
bundle legacy. API CCTV mới có `/ready` riêng, đã kiểm thử với index và MP4 previews
theo `CCTV_DEMO_UI.md`. Không dùng `/health` hoặc việc tạo index làm bằng chứng `/ready` đã đạt.

Chín test mới dùng RGB synthetic encoder, kiểm tra persistence/locator, resume không
nạp model/không rewrite, interruption theo transaction, raw/output protection,
corrupt vector, lỗi từng ảnh, OOM splitting và gián đoạn publication. Chúng không đo chất lượng CLIP.
Benchmark retrieval cần queries và relevance labels độc lập; demo query không
thay Recall@K/mAP. Phát hiện tai nạn, tracking, mật độ và phân bổ nhân lực thuộc
milestone sau.

## Kết quả trên demo ngày 2026-09-30

Đã index đủ 7,232 frame, dimension 768; `frames`, `cctv_frames`, `cctv_vectors`
và FAISS có cùng coverage. Lần chạy tiếp xử lý 6,012 frame và bỏ qua 1,220 vectors
đã commit trước gián đoạn. Không có frame lỗi. Peak CUDA đo bằng PyTorch ở lần
chạy tiếp: allocated 874.02 MiB, reserved 898.00 MiB với batch 4 (không phải tổng
VRAM của toàn bộ Windows hoặc các ứng dụng khác).

Core suite trên Windows PowerShell 5.1: 37 tests đạt, cùng runtime CUDA FP16/NMS.
Lần thử chạy runtime verification đồng thời với indexing gặp lỗi thiếu bộ nhớ
phân trang Windows; sau khi model được giải phóng, toàn bộ kiểm tra trên đã đạt.
Không đổi cấu hình pagefile hệ thống; chạy các stage model/test tuần tự.
Formatter/lint đã kiểm tra năm file Python mới; formatter chỉ cài trong output
tạm, không thay dependency runtime hoặc lockfile.

Resume sau hoàn tất: `done=0, skipped=7232, error=0, model_loaded=false`;
kích thước/mtime của database, FAISS, sidecar và index manifest không đổi.
CLI stored-vector tìm lại `N001-V001/500.jpg` ở top 1 với score 1.0,
timestamp 249.519 giây, decoded frame index 6238, camera N001.

Stored-vector round-trip qua cả sáu video, mỗi video kiểm tra frame 500 và tìm đúng
top 1. Image query bằng CLIP từ `N001-V001/500.jpg` cũng tìm đúng ảnh nguồn ở top 1
(score > 0.999). Hai query text đã chạy với cùng instance CLIP:

- `a busy road intersection at night with headlights`: top 1 `N002-V001/1181.jpg`.
- `motorbikes waiting at a traffic light during the day`: top 1 `N001-V001/654.jpg`.

Đây là smoke test, chưa có relevance labels để chấm Recall@K/mAP hoặc xác nhận
"waiting"/mức độ congestion. Database có 7,232 rows ở cả ba bảng frames/CCTV/vector;
video gốc vẫn khớp SHA-256 và mtime sau indexing/retrieval.
Báo cáo đầy đủ: `outputs/demo-data-review/retrieval-verification.json` (không commit).

## Git

```powershell
git diff --check
git add -- pipeline/cctv/clip.py pipeline/cctv/index.py pipeline/cctv/search.py scripts/download_cctv_clip.py test_cctv_index.py scripts/Test-Windows.ps1 docs/CCTV_RETRIEVAL.md README_CCTV.md docs/DEMO_DATA.md
git diff --cached --check
git commit -m "feat: add resumable CLIP retrieval for CCTV demo"
git push origin main
```
