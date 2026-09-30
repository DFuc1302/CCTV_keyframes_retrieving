# Tài liệu kế thừa và phát triển đồ án tìm kiếm video đa phương thức

Ngày cập nhật: 30 tháng 9 năm 2026  
Mã nguồn được khảo sát: `D:\AIC_26\CODE`  
Đối tượng sử dụng: sinh viên hoặc nhóm sinh viên tiếp nhận project để làm đồ án.

Tài liệu này hướng dẫn tiếp nhận hệ thống, hiểu các thành phần, chạy lại với dữ liệu đã xử lý và phát triển thành một đồ án có đóng góp riêng. Hướng đề xuất là **hệ thống tìm kiếm nội dung video bài giảng bằng mô tả hình ảnh, chữ trên slide và lời nói**. Nên bắt đầu bằng một bộ dữ liệu nhỏ, chạy được toàn bộ quy trình, rồi mới mở rộng số video và thuật toán.

Các tính năng ghi là “hiện có” được đối chiếu với mã nguồn và tài liệu trong project. Các quy mô, lịch làm việc và tính năng ghi là “đề xuất” là kế hoạch cho đồ án, chưa phải kết quả đã thực hiện.

## 1 Mô tả project hiện tại

Project là một hệ thống tìm kiếm video đa phương thức. Hệ thống biến video thành các khung hình đại diện, nhận dạng chữ và lời nói, lập chỉ mục rồi cung cấp giao diện tìm kiếm trên trình duyệt.

Ví dụ, người dùng có thể tìm một cảnh bằng mô tả “giảng viên đang viết lên bảng”, tìm chữ “binary search” trên slide, hoặc tìm đoạn lời nói chứa “độ phức tạp thuật toán”. Ba cách tìm dựa trên các nguồn thông tin khác nhau; chất lượng thực tế cần đo trên dữ liệu của đồ án, đặc biệt với truy vấn tiếng Việt và thuật ngữ chuyên ngành.

| Thành phần | Vai trò hiện có | Giá trị khi kế thừa |
| --- | --- | --- |
| FastAPI | Cung cấp API tìm kiếm, metadata, trạng thái và giao diện | Có nền backend để phát triển chức năng mới |
| HTML CSS JavaScript | Giao diện tìm kiếm, xem kết quả, shortlist và truy vấn đã lưu | Có thể đổi sang luồng tra cứu bài giảng |
| CLIP và FAISS | Biểu diễn nội dung hình ảnh và truy hồi vector | Tái sử dụng tìm kiếm cảnh theo mô tả |
| OCR | Nhận dạng và lập chỉ mục chữ trong khung hình | Tra cứu slide, bảng viết và tiêu đề |
| Whisper và ASR | Chuyển lời nói thành văn bản có mốc thời gian | Tra cứu nội dung giảng viên nói |
| SQLite | Metadata, ánh xạ khung hình và các chỉ mục văn bản | Phù hợp với bản thử nghiệm trên một máy |
| Redis | Backend tùy chọn cho dữ liệu và tìm kiếm văn bản | Mở rộng sau khi bản cơ bản hoạt động |
| Telemetry và evaluator | Ghi sự kiện tìm kiếm và tính chỉ số từ nhãn độc lập | Có nền để đánh giá đóng góp của đồ án |

Ngoài ra, mã nguồn có tìm khung hình tương tự, Video Match, timeline, tìm kiếm theo phiên KIS, summary và bộ lọc đối tượng hoặc màu. Không cần đưa tất cả vào phạm vi nghiệm thu ban đầu.

## 2 Đề tài đề xuất cho trường

**Tên đề tài:** Xây dựng hệ thống tìm kiếm nội dung video bài giảng đa phương thức.

**Bài toán:** Sinh viên thường phải tua qua nhiều đoạn video để tìm lại một khái niệm, slide hoặc phần giải thích. Tìm theo tên video chưa đủ để định vị nội dung nằm bên trong video. Đồ án xây dựng công cụ trả về khung hình hoặc đoạn lời nói liên quan, kèm video và mốc thời gian để người dùng kiểm tra.

**Người dùng chính:** Sinh viên tra cứu bài học và giảng viên kiểm tra nội dung trong kho video đã được phép sử dụng.

**Phạm vi đề xuất:** 20–50 video của một hoặc hai môn học; 60–100 truy vấn được gán nhãn để đánh giá. Đây là quy mô khởi đầu, điều chỉnh sau khi đo thời gian xử lý và dung lượng trên máy của nhóm.

**Sản phẩm tối thiểu:** Một ứng dụng chạy trên máy demo, bộ dữ liệu thử nghiệm, pipeline tạo chỉ mục, tài liệu cài đặt và báo cáo thực nghiệm. Mỗi kết quả phải cho biết nguồn video và vị trí nội dung tìm được.

### Phần kế thừa và phần đóng góp mới

| Kế thừa từ project | Phần nhóm cần thực hiện cho đồ án |
| --- | --- |
| Backend FastAPI và giao diện nền | Luồng sử dụng dành cho video môn học |
| Trích khung hình, OCR, ASR, CLIP, FAISS | Chuẩn hóa để chạy với video mới có tên bất kỳ |
| Metadata và các chỉ mục | Bổ sung môn học, bài học và thông tin nguồn |
| Các nhánh truy hồi hiện có | Thực nghiệm so sánh và cải tiến một nhánh cụ thể |
| Evaluator và telemetry | Bộ truy vấn có nhãn, cấu hình thí nghiệm và kết quả tái lập |

Đóng góp riêng nên tập trung vào **pipeline dùng được với dữ liệu mới, bộ benchmark bài giảng và một cải tiến retrieval có số liệu**. Đổi tên hoặc đổi màu giao diện là phần hoàn thiện sản phẩm; chưa đủ để chứng minh đóng góp kỹ thuật.

Một hướng cải tiến vừa sức là gộp thứ hạng từ OCR, ASR và CLIP để tra cứu cùng một bài giảng. Muốn so sánh công bằng, cần chuẩn hóa về cùng đơn vị kết quả, chẳng hạn đoạn video theo thời gian. Không cộng trực tiếp điểm OCR, điểm vector và điểm ASR khi thang đo của chúng khác nhau.

## 3 Kiến trúc và bản đồ mã nguồn

```text
Video đầu vào
  -> kiểm tra danh sách video và định danh
  -> trích khung hình và bản đồ thời gian
  -> loại khung hình trùng nếu cần
  -> OCR và embedding hình ảnh
  -> ASR từ âm thanh
  -> SQLite và FAISS, Redis tùy chọn
  -> đóng gói và kiểm tra serving bundle
  -> FastAPI
  -> giao diện tìm kiếm trên trình duyệt
```

Tách hai việc: pipeline xử lý dữ liệu và ứng dụng phục vụ tìm kiếm. Server demo nên đọc bộ chỉ mục hoàn chỉnh; không tự xử lý lại toàn bộ video lúc khởi động.

| Đường dẫn trong CODE | Nên đọc khi làm gì |
| --- | --- |
| `README.md` | Hiểu cấu trúc và cách tiếp cận project |
| `app/main.py` | Tìm API, vòng đời ứng dụng và logic tìm kiếm |
| `app/shared.py` | Cấu hình đường dẫn, model, SQLite và vector |
| `app/services/` | Dịch vụ Redis, telemetry, health, KIS và reranker |
| `pipeline/frames/extract.py` | Trích khung hình và map thời gian |
| `pipeline/ocr/index.py` | Lập chỉ mục ảnh, OCR và thông tin đối tượng |
| `pipeline/asr/index.py` | Xử lý transcript và mốc thời gian |
| `pipeline/index/bundle.py` | Tạo manifest và dữ liệu phục vụ truy vấn |
| `pipeline/run.py` và `pipeline/new_dataset.py` | Điều phối nhiều bước, cần kiểm tra phụ thuộc dữ liệu cũ |
| `index.html` và `static/` | Sửa giao diện và cách gọi API |
| `evaluate_search.py` và `benchmark/README.md` | Thiết kế và chạy đánh giá retrieval |
| `requirements/` | Các điểm vào cài dependency |
| `docs/BASELINE_VALIDATION.md` | Đọc tình trạng kiểm thử đã ghi nhận |
| `legacy/aic-2026/` | Công cụ lịch sử phục vụ cuộc thi |

Các file ở thư mục gốc như `main.py`, `shared.py`, `ocr_index.py` vẫn được dùng làm lớp tương thích cho những lệnh và import cũ. Không xóa chỉ vì đã thấy file tương ứng trong package.

### Các API cần biết

| API | Mục đích |
| --- | --- |
| `GET /health` | Thông tin ứng dụng và model; kiểm tra thêm trường ready |
| `GET /ready` | Sẵn sàng phục vụ; trả 200 khi ready và 503 khi chưa ready |
| `GET /status` và `GET /data-health` | Xem trạng thái và vấn đề dữ liệu |
| `GET /search` | Tìm theo mô tả, OCR và các bộ lọc |
| `POST /asr/search` | Tìm nội dung lời nói |
| `GET /similar` | Tìm khung hình tương tự |
| `GET /frame/meta/{filename}` | Đọc metadata của khung hình |
| `GET /frame/timeline` | Tra cứu timeline |
| `GET /summary/search` | Tìm summary nếu dữ liệu và model đã cấu hình |

Xem schema tham số tại `/docs` sau khi server chạy. Đường dẫn API hiện có không đồng nghĩa mọi chức năng đã có dữ liệu để sử dụng trên máy mới.

## 4 Những thứ cần bàn giao

**Mã nguồn:** Toàn bộ `CODE`, gồm `app/`, `pipeline/`, `static/`, `index.html`, requirements, tests, tài liệu và các file tương thích đang được dùng. Ghi lại commit hoặc một bản snapshot để biết đồ án bắt đầu từ phiên bản nào.

**Dữ liệu:** Video hoặc bộ khung hình, map thời gian, database, FAISS index, ASR cache và serving manifest. Nếu nhận bộ index đã xử lý, phải nhận cả ảnh tương ứng; database và vector không thay thế được ảnh dùng để hiển thị.

**Model:** Đúng model đã tạo embedding, tokenizer và cấu hình tương ứng. `CLIP_MODEL` phải khớp với model trong manifest của bộ index. Thay model embedding cần xây lại vector tương ứng, không chỉ đổi biến cấu hình.

**Thông tin chạy:** Hệ điều hành, phiên bản Python, dependency đã cài, thiết bị CPU hoặc GPU, các đường dẫn và kết quả kiểm tra readiness. Chỉ mục lớn, video và model nên nằm ngoài repository mã nguồn.

Tài liệu `UPLOAD_FOR_TEAM.md` ở `C:\PERSONAL_PROJECT\AIC_26` mô tả cách bàn giao cũ gồm `CODE_FOR_TEAM`, `DATA_FOR_TEAM` và `filtered_data_all_873_moved`. Đây là thông tin về gói trước đây; cần kiểm tra các folder thực tế trước khi dùng. Mã nguồn đang khảo sát ở ổ D không kèm đủ dữ liệu chỉ vì nó có launcher.

**Thông tin nguồn:** Ghi tác giả hoặc nhóm phát triển ban đầu, các thành phần được sử dụng lại và phần do nhóm đồ án tự làm. Kiểm tra điều kiện sử dụng mã nguồn, model và video trước khi chia sẻ sản phẩm; hiện chưa thấy file LICENSE ở cấp gốc `CODE`.

## 5 Chạy lại bản tìm kiếm với bộ dữ liệu đã xử lý

Các lệnh dưới đây là hướng dẫn thực hiện trong **WSL hoặc Linux**, từ thư mục `CODE`. Chúng chưa được chạy end to end trong lần biên soạn tài liệu này. Cần có Python, ffmpeg khi xử lý media, và bộ serving bundle hợp lệ để khởi động tìm kiếm.

### Cài môi trường riêng

```bash
cd /mnt/d/AIC_26/CODE
python3 -m venv /mnt/d/school_video_project/.venv
source /mnt/d/school_video_project/.venv/bin/activate
python -m pip install -r requirements/runtime.txt
```

Tạo thư mục cha `/mnt/d/school_video_project` nếu chưa có. Các file `runtime.txt` và `pipeline.txt` hiện cùng tham chiếu danh sách `requirements_wsl.txt` khá rộng; việc chia dependency theo nhiệm vụ chưa phải tối ưu tối giản. Danh sách này cũng chưa khóa mọi phiên bản. Sau khi cài và kiểm thử thành công, lưu phiên bản môi trường của đồ án.

### Cấu hình tìm kiếm cơ bản với SQLite

Ví dụ dưới đây giả định bộ index nằm trong `search_index`, khung hình nằm trong `extracted/keyframes`, còn map nằm trong `extracted/map-keyframes`. Đổi đường dẫn theo bộ dữ liệu thực tế. Không dùng cấu hình serving này cho bước xây chỉ mục vì nó bật chế độ chỉ đọc.

```bash
export DATA_ROOT=/mnt/d/school_video_project/data/search_index
export KEYFRAMES_DIR=/mnt/d/school_video_project/data/extracted/keyframes
export MAPFILES_DIR=/mnt/d/school_video_project/data/extracted/map-keyframes
export VIDEOS_DIR=/mnt/d/school_video_project/data/videos
export META_DB="$DATA_ROOT/meta.db"
export FAISS_DIR="$DATA_ROOT/faiss_index"
export ASR_CACHE_FILE="$DATA_ROOT/asr_cache.json"
export BACKEND_MODE=sqlite
export STORAGE_BACKEND=sqlite
export CLIP_MODEL=openai/clip-vit-large-patch14
export CLIP_TEXT_ONLY=1
export CLIP_TEXT_MODEL_PATH="$DATA_ROOT/clip_text"
export SERVE_READ_ONLY=1
export REQUIRE_SERVE_BUNDLE=1
export AUTO_INDEX_ON_STARTUP=0
export ALLOW_HEAVY_RUNTIME_JOBS=0
export OCR_ON_DEMAND=0
export FEDERATED_DATASET_CONFIG=''
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Giá trị CLIP ở ví dụ là mặc định trong mã nguồn; xác nhận lại `serve_manifest.json` trước khi chạy. Chế độ text only yêu cầu artifact `clip_text` đã được xuất. Không bật chế độ offline nếu model cần dùng chưa có trên máy. Với cấu hình cơ bản này, chưa nên lấy summary hoặc chức năng Redis làm điều kiện nghiệm thu.

Mở `http://127.0.0.1:8080` trên máy đang chạy. Kiểm tra `/ready` trả 200, tìm một nội dung đã biết, mở ảnh kết quả rồi đối chiếu video và mốc thời gian. Việc `/health` trả lời chưa đủ để kết luận hệ thống tìm kiếm đã sẵn sàng.

### Khi sử dụng gói team cũ

`run_local_server_wsl.sh` thiết lập hybrid và gọi `team_runtime.py`. Wrapper này yêu cầu bố cục cụ thể của `DATA_FOR_TEAM`, model summary, Redis runtime và các Redis module. Nó không phải launcher SQLite chung cho một bộ dữ liệu bất kỳ. Chỉ dùng sau khi đã nhận đủ gói dữ liệu đúng cấu trúc.

## 6 Đưa video của trường vào hệ thống

### Quy trình tiếp nhận dữ liệu mới

1. Chọn 3–5 video thử nghiệm có nguồn sử dụng rõ ràng. Đặt ID video duy nhất và lưu tên bài học, môn học, đường dẫn, thời lượng.
2. Trích khung hình và map thời gian vào thư mục riêng. Giữ video đầu vào để có thể đối chiếu.
3. Kiểm tra ảnh và map trước khi dedup. Sau dedup, map chỉ được tham chiếu các ảnh còn tồn tại.
4. Tạo embedding, OCR và metadata. Ghi model cùng tham số xử lý trong hồ sơ bộ dữ liệu.
5. Chạy ASR riêng; kiểm tra thuật ngữ, tiếng Việt có dấu và mốc thời gian trên một số đoạn đã biết.
6. Tạo serving bundle; kiểm tra số lượng vector, metadata, ảnh và transcript.
7. Chạy server chỉ đọc và thực hiện các truy vấn đối chứng.

Lệnh trích khung hình đã có tham số cho dữ liệu mới, ví dụ:

```bash
python -m pipeline.frames.extract \
  --input-dir /mnt/d/school_video_project/data/videos \
  --output-dir /mnt/d/school_video_project/data/extracted \
  --device cpu --workers 2
```

Module OCR có thể chạy qua `python -m pipeline.ocr.index`; module ASR nhận `--folder` qua `python -m pipeline.asr.index --folder ...`; module bundle nhận `--data-root`. Các bước này cần cấu hình đường dẫn, model và quyền ghi phù hợp cho giai đoạn xử lý. Nên viết launcher riêng cho đồ án sau khi kiểm chứng trên tập nhỏ, thay vì nối các lệnh trong môi trường serving chỉ đọc.

### Các phụ thuộc cũ cần sửa trước khi dùng pipeline tổng

Trong `pipeline/run.py`, mã hiện tại còn giới hạn tên video dạng `S01_V...` hoặc `S01-V...`, một category cố định, yêu cầu manifest của index cũ và kiểm tra CUDA. `pipeline/new_dataset.py` chủ yếu phục vụ gộp dữ liệu cũ và mới, yêu cầu CUDA trong preflight và không tự tạo ASR. Do đó, đổi các đường dẫn mặc định chưa đủ để xử lý kho bài giảng mới từ đầu.

Các việc cần làm cho đồ án:

- Thay nhận diện tên S01 bằng manifest chứa ID video của trường.
- Nhận category hoặc môn học từ cấu hình, bỏ giá trị cố định theo bộ dữ liệu cũ.
- Cho phép khởi tạo một dataset đầu tiên không cần `old-data-root`.
- Phân biệt stage hỗ trợ CPU với stage bắt buộc GPU; thông báo điều kiện thiếu ngay từ đầu.
- Tạo bước ASR rõ ràng trong luồng mới và kiểm tra coverage transcript.
- Xuất trạng thái từng bước để chạy tiếp sau khi gián đoạn.

Đây là phần đóng góp có giá trị thực tế: chứng minh cùng hệ thống có thể tiếp nhận một tập video mới, thay vì chỉ chạy lại dữ liệu cuộc thi.

## 7 Phạm vi phát triển nên chọn

### Bản tối thiểu để bảo vệ

- Danh mục môn học và bài giảng gắn với video ID.
- Tìm nội dung qua OCR, ASR hoặc mô tả hình ảnh.
- Hiển thị nguồn, khung hình hoặc transcript và mốc thời gian.
- Mở video đến vị trí liên quan nếu có video gốc; kiểm tra và bổ sung luồng này nếu giao diện hiện tại chưa đáp ứng.
- Trạng thái rõ ràng khi đang tải, không có kết quả hoặc dữ liệu chưa sẵn sàng.
- Pipeline tái tạo chỉ mục và bộ truy vấn đánh giá độc lập.

### Chọn một hướng cải tiến chính

| Hướng | Công việc | Bằng chứng cần có |
| --- | --- | --- |
| Gộp nhiều nguồn tìm kiếm | Chuẩn hóa đơn vị kết quả rồi kết hợp thứ hạng OCR ASR CLIP | So sánh từng nhánh và bản kết hợp |
| Nâng chất lượng OCR slide | Thử tiền xử lý ảnh hoặc cách chọn vùng chữ | Chất lượng văn bản và retrieval trên cùng tập kiểm tra |
| Tìm theo đoạn thời gian | Gộp frame gần nhau và transcript theo timestamp | Độ chính xác tìm đúng đoạn, không chỉ đúng video |
| Cải thiện tiếng Việt | Thử chuẩn hóa hoặc mở rộng truy vấn có kiểm soát | So sánh truy vấn có dấu, không dấu và thuật ngữ |

Nếu chỉ có một học kỳ, ưu tiên một hướng và hoàn thành thực nghiệm. Tài khoản, phân quyền, chatbot hỏi đáp, upload nhiều người dùng và triển khai cho cả trường có thể để giai đoạn sau. Các tính năng này chưa được xem là đã có trong project chỉ vì có một giao diện tìm kiếm.

## 8 Thiết kế đánh giá để đưa vào báo cáo

### Tạo nhãn độc lập

Chia video thành tập phát triển và tập kiểm tra trước khi tối ưu thuật toán. Tránh để những frame gần nhau của cùng video nằm ở cả hai tập nếu chúng được dùng cho huấn luyện hoặc lựa chọn cấu hình.

Tạo truy vấn thuộc ba nhóm: mô tả cảnh, chữ trong hình và lời nói. Mỗi truy vấn ghi loại, chủ đề, đơn vị đánh giá và danh sách kết quả liên quan do người gán nhãn xác nhận. Shortlist của người dùng chỉ là ứng viên để duyệt, không tự trở thành nhãn đúng.

Evaluator nhận qrels dưới dạng **một danh sách JSON**, ví dụ minh họa:

```json
[
  {
    "query_id": "THAY_BANG_QUERY_ID_TU_LOG",
    "unit": "frame",
    "query_type": "text-in-scene",
    "topic": "algorithms",
    "relevant": ["LECTURE_001/000123.jpg"]
  }
]
```

ID kết quả phải khớp đúng ID trong ranking của log. `query_id` là ID từ telemetry, không phải tên truy vấn tùy đặt. Log hiện dùng HMAC cho văn bản; cần giữ khóa ổn định và lập bảng đối chiếu truy vấn đánh giá với ID sinh ra. Nếu các lần chạy đổi khóa hoặc bộ tham số nhận diện truy vấn, phải đối chiếu lại trước khi so sánh.

Đánh giá khung hình dùng `unit=frame`, ASR dùng `unit=segment`, Video Match dùng `unit=video`. Không so sánh các chỉ số ở những đơn vị khác nhau như thể cùng một bài toán.

### Chỉ số và thí nghiệm

Đo Recall@5 và Recall@10 để biết tỷ lệ các kết quả được gán nhãn liên quan xuất hiện trong top K. Đo MRR để biết kết quả đúng đầu tiên nằm cao đến đâu. Ghi P50 và P95 latency, tỷ lệ không kết quả, số truy vấn được chấm và các truy vấn lỗi.

Đo riêng thời gian phản hồi API và trải nghiệm hoàn tất hiển thị trên trình duyệt; latency trong log backend không bao gồm toàn bộ quá trình người dùng xem kết quả. Ghi cấu hình máy, model, số frame, index và phân biệt lượt chạy nóng với khởi động lạnh.

Thí nghiệm tối thiểu: CLIP riêng, OCR riêng, ASR riêng và phương án kết hợp do nhóm triển khai. Giữ nguyên tập truy vấn và nhãn; tối ưu trên tập phát triển rồi đóng băng cấu hình trước khi chạy test. Nhánh kết hợp hiện tại không được mặc định gọi là BM25 thuần. Repo chưa có giao diện thí nghiệm chuẩn để bật tắt mọi nhánh độc lập.

```bash
python evaluate_search.py \
  --logs /mnt/d/school_video_project/runtime_logs \
  --qrels /mnt/d/school_video_project/benchmark/qrels-test.json \
  --output /mnt/d/school_video_project/benchmark/report.json
```

Đường dẫn log phải trỏ tới log thật của lần chạy. Evaluator đọc các file `search-*.jsonl`, báo hash SHA-256 của qrels và phân biệt cấu hình bằng `configuration_id`. Nó bỏ qua một số lượt thiếu nhãn hoặc thiếu trang ranking; phải kiểm tra số truy vấn thực sự được chấm, không chỉ nhìn giá trị trung bình.

Nếu đánh giá đúng đoạn thời gian, ranking cần có `video_id` và `timestamp_seconds`. Tài liệu benchmark hiện ghi log khung hình còn ở dạng chuỗi; cần kiểm tra và bổ sung xuất timestamp trước khi công bố temporal metrics. Lấy thời gian từ map `pts_time`, không suy ra bằng cách chia số thứ tự ảnh cho FPS giả định.

Không đặt sẵn kết luận “cải thiện X phần trăm”. Báo cáo cả trường hợp thất bại như chữ nhỏ, bảng viết tay, âm thanh nhiễu và mô tả tiếng Việt không khớp hình ảnh.

## 9 Lộ trình đề xuất trong tám tuần

| Tuần | Trọng tâm | Điều kiện hoàn thành |
| --- | --- | --- |
| 1 | Tiếp nhận mã và dữ liệu, thống nhất đề tài | Có snapshot nguồn, kiểm kê dữ liệu và chạy readiness với bundle hợp lệ |
| 2 | Chạy 3–5 video mới | Có ảnh, map, OCR, ASR và index; truy vấn đối chứng trả đúng nguồn |
| 3 | Chuẩn hóa pipeline | Dùng video tên bất kỳ và chạy lại không phụ thuộc dataset cũ |
| 4 | Hoàn thiện giao diện môn học | Tra cứu và kiểm tra được vị trí kết quả trên video |
| 5 | Triển khai một cải tiến | Có cấu hình baseline và cấu hình mới để so sánh |
| 6 | Gán nhãn và đóng băng benchmark | Có qrels được duyệt, split và hash dữ liệu kiểm tra |
| 7 | Thực nghiệm và phân tích lỗi | Có bảng chất lượng, latency và số truy vấn được chấm |
| 8 | Đóng gói và chuẩn bị bảo vệ | Máy thứ hai chạy lại được; có báo cáo, slide và kịch bản demo |

Nếu làm nhóm ba người, có thể chia theo pipeline và dữ liệu; backend và thuật toán; giao diện và trải nghiệm. Cả nhóm cùng duyệt nhãn và đọc kết quả đánh giá. Nếu làm một mình, giảm số video và chỉ chọn một cải tiến chính.

## 10 Checklist nghiệm thu và rủi ro thực tế

### Tiếp nhận và chạy hệ thống

- [ ] Có mã nguồn, snapshot hoặc commit và ghi nhận nguồn kế thừa.
- [ ] Có đủ ảnh, map, metadata, vector và model tương ứng.
- [ ] Đường dẫn được cấu hình, không phụ thuộc tài khoản của người bàn giao.
- [ ] `/ready` trả 200 và truy vấn đối chứng hoạt động.
- [ ] Kết quả mở được ảnh và đối chiếu được mốc thời gian.

### Phát triển và đánh giá

- [ ] Một tập video mới đi qua đầy đủ pipeline.
- [ ] Có phần đóng góp riêng được mô tả và đo lường.
- [ ] Qrels được gán nhãn độc lập và tập test được đóng băng.
- [ ] Có số liệu baseline, cấu hình cải tiến, latency và phân tích lỗi.
- [ ] Chạy lại được trên môi trường thứ hai từ hướng dẫn đã ghi.

### Những rủi ro cần xử lý trong quá trình làm

| Rủi ro | Cách xử lý |
| --- | --- |
| Đổi tên hoặc vị trí dữ liệu làm mất ảnh | Kiểm tra frame locator, map và đường dẫn sau khi di chuyển |
| Model và index không tương thích | Đối chiếu manifest; xây lại vector khi đổi model |
| Xử lý quá nhiều video ngay từ đầu | Thử 3–5 video, đo chi phí rồi mới mở rộng |
| OCR hoặc ASR sai thuật ngữ | Gán nhãn mẫu, phân tích lỗi và báo coverage theo video |
| Máy không có CUDA | Dùng stage hỗ trợ CPU hoặc xử lý trước trên máy phù hợp |
| Kết quả test cũ bị hiểu là của phiên bản mới | Chạy lại kiểm thử trong môi trường đồ án và lưu log mới |
| Bộ dữ liệu chưa được phép chia sẻ | Chọn video được chấp thuận và ghi nguồn sử dụng |

`docs/BASELINE_VALIDATION.md` hiện ghi 9 kiểm thử tập trung đạt; lần discovery được mô tả có 23 test, với 8 test không tải được do thiếu dependency và 1 test còn bộc lộ hành vi ngoài phạm vi cleanup. Đây là trạng thái đã được ghi trong tài liệu, chưa được tái xác nhận ở lần biên soạn này. Không lấy các báo cáo tháng 9 ở thư mục khác làm bằng chứng tất cả test của bản hiện tại đều đạt.

Trong môi trường đồ án đã cài dependency, chạy `python -m compileall -q .` và `python -m unittest discover -v`, xử lý lỗi thực tế rồi lưu kết quả. Sau đó kiểm tra tìm kiếm với dữ liệu thật, trình duyệt hẹp và thao tác bàn phím.

## 11 Khung báo cáo và đề cương có thể sử dụng

### Cấu trúc báo cáo

1. **Giới thiệu:** Vấn đề tìm nội dung trong bài giảng, mục tiêu và phạm vi.
2. **Cơ sở lý thuyết:** Keyframe, OCR, ASR, embedding, vector search và chỉ số retrieval.
3. **Phân tích và thiết kế:** Người dùng, luồng tra cứu, kiến trúc và hợp đồng dữ liệu.
4. **Cài đặt:** Thành phần kế thừa, các sửa đổi pipeline và cải tiến của nhóm.
5. **Thực nghiệm:** Dataset, nhãn, split, cấu hình, kết quả và phân tích lỗi.
6. **Kết luận:** Mức độ đạt mục tiêu, giới hạn và hướng mở rộng.

### Đoạn mô tả đề tài để chỉnh sửa và gửi giảng viên

Đề tài xây dựng hệ thống tìm kiếm nội dung video bài giảng dựa trên mô tả hình ảnh, văn bản xuất hiện trong video và lời nói. Hệ thống kế thừa nền tảng tìm kiếm video đa phương thức gồm FastAPI, OCR, ASR, CLIP và FAISS; từ đó chuẩn hóa pipeline để tiếp nhận dữ liệu bài giảng mới, bổ sung thông tin môn học và cung cấp kết quả gắn với vị trí trong video. Phần thực nghiệm xây dựng bộ truy vấn được gán nhãn độc lập, so sánh các nguồn truy hồi và đánh giá một phương án kết hợp kết quả. Sản phẩm dự kiến gồm ứng dụng demo, pipeline xử lý dữ liệu, tài liệu tái lập và báo cáo chất lượng tìm kiếm cùng thời gian phản hồi. Quy mô dữ liệu và phạm vi cải tiến được thống nhất với giảng viên sau giai đoạn thử nghiệm ban đầu.

### Thông tin cần điền khi đăng ký đề tài

- Trường, khoa, môn học và giảng viên hướng dẫn.
- Thành viên, thời hạn và tiêu chí chấm đồ án.
- Nguồn video được phép sử dụng.
- Cấu hình máy xử lý dữ liệu và máy demo.
- Hướng cải tiến chính và sản phẩm bắt buộc phải nộp.

## 12 Tài liệu nguồn để người tiếp nhận đọc tiếp

Các đường dẫn dưới đây tính từ thư mục `CODE`, trừ nhóm báo cáo ở ổ C.

- `README.md`, `docs/ARCHITECTURE.md` và `docs/PIPELINE_MAP.md`: kiến trúc và luồng dữ liệu.
- `pipeline/README.md`, `requirements/README.md` và `docs/CLEANUP_GUIDE.md`: vận hành và quản lý dữ liệu ngoài mã nguồn.
- `app/main.py`, `app/shared.py` và `app/services/redis_store.py`: API và cấu hình thực tế.
- `pipeline/run.py`, `pipeline/new_dataset.py`, `pipeline/frames/extract.py`, `pipeline/ocr/index.py`, `pipeline/asr/index.py`, `pipeline/index/bundle.py`: hành vi các bước xử lý.
- `benchmark/README.md`, `evaluate_search.py` và `app/services/telemetry.py`: hợp đồng log, qrels và phương pháp đánh giá.
- `docs/BASELINE_VALIDATION.md`: trạng thái kiểm thử được ghi nhận cho cấu trúc hiện tại.
- `C:\PERSONAL_PROJECT\AIC_26\UPLOAD_FOR_TEAM.md`, `AUDIT_REPORT.md`, `REFACTOR_REPORT.md`: lịch sử bàn giao và các đợt kiểm tra trước.

Bước bắt đầu cụ thể: kiểm kê bộ dữ liệu nhận được, chạy bản chỉ đọc với một bundle hợp lệ, rồi thử pipeline trên 3–5 video bài giảng. Sau khi ba việc này hoàn tất, chốt phạm vi cải tiến và lịch thực nghiệm với giảng viên.
