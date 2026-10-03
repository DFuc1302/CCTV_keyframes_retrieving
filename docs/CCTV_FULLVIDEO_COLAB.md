# Toàn video, tốc độ và Colab batch

## Phạm vi

UI mặc định tracking **toàn video**, lấy frame theo PTS mỗi **0,5 giây**.
Chế độ **khoảng** vẫn giữ start/duration 1–60 giây; tìm kiếm/keyframe/index
luôn dùng toàn video. **Mọi frame** là lựa chọn khi cần tracking dày hơn.
2 FPS giữ timestamp và ordinal của frame gốc, không giả định video có FPS cố định.
Overlay 2 FPS có thể giật, bỏ lỡ chuyển động nhanh; ID và rules chưa hiệu chỉnh
cho dữ liệu thật. ROI và M8 vẫn pending, dispatch chỉ mô phỏng.

Cold Windows UI smoke lịch sử gặp native access violation khi CLIP load checkpoint,
không phải batch YOLO OOM. Probe đo Windows1455/commit exhaustion; hiện serving
CLIP thuộc process riêng, thoát trước stage GPU, với bounded native thread defaults.
Fresh browser upload H.264 test-pattern 12 giây đã đạt 12/12 lần đầu, không
standalone indexing/retry; weights, FP16, loader và pagefile không thay.
Đây là cold local smoke, không chứng minh quality/SLA. Logs và gates:
[progress](CCTV_PROGRESS.md#m11--cold-ui-pipeline-đạt-sau-sửa-ownershipcommit--2026-10-03).


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

### Code identity Windows → Linux — 2026-10-02

Kiểm tra runtime thực phát hiện checkout Windows CRLF và Git/Linux LF tạo
code hashes khác nhau dù cùng executable source. `code_identity` hiện chỉ
chuẩn hóa CRLF→LF trước SHA256; thay đổi executable vẫn bị từ chối.
Checksums ZIP/raw/model không chuẩn hóa và vẫn kiểm tra bytes nguyên gốc.
Xuất job mới sau thay đổi này; không sửa metadata/checksum của package cũ.

Importer cũng rebind source dependency hashes LF/CRLF sang bytes checkout local
sau khi code identity đã khớp, trước khi reseal JSON/SQLite và các checksum
phụ thuộc. Chỉ source code được rebind; ZIP/raw/model vẫn byte-exact.
Regression kiểm tra signature JSON/SQLite theo cả hai hướng LF↔CRLF và từ
chối executable đã đổi; 11 cloud/workflow tests đạt:
`outputs/tests/colab-source-newline-variants-tests-20261002.txt`.

10 cloud/workflow tests đạt; smoke export/unpack trên video integration thật
xác nhận 55 source identities khớp bản LF và reject source executable đổi.
Evidence: `outputs/tests/colab-portability-tests-20261002.txt`,
`outputs/tests/colab-portability-smoke-20261002.json`.
Đã kết nối Colab T4 runtime 2025.07/Python 3.11.13, mount Drive và kiểm tra
job. GitHub clone chưa có authentication trả 404; demo GPU→UI dùng source
archive từ exact commit, kiểm tra SHA256 và code identity trước execution.

Colab native smoke cũng phát hiện `output_lease` chỉ hỗ trợ Windows.
Lease hiện dùng Windows byte-range lock hoặc POSIX `flock`, đều nonblocking
và do OS giải phóng khi process kết thúc. Smoke Tesla T4/Linux xác nhận
writer cạnh tranh bị chặn, acquire được sau release và sau owner exit.
11 ingestion tests đạt trên Windows; 10 cloud/workflow tests đạt sau khi
dừng owned local server để giải phóng memory (lượt đầu lỗi WinError 1455/1450).
Logs: `outputs/tests/colab-linux-lease-smoke-20261002.txt`,
`colab-lease-tests-20261002.txt`, `colab-lease-recovered-tests-20261002.txt`.

Notebook tạo venv bằng `--without-pip --system-site-packages` vì ensurepip
không hoạt động trong runtime đã chọn. Pip dùng module có sẵn; sau install,
chỉ gỡ NVIDIA packages nằm trong venv để không shadow CUDA stack của Colab.
Không gỡ packages global hoặc thay Torch/torchvision được Colab cung cấp.
Runtime thực Python 3.11.13/Torch 2.6.0+cu124/Tesla T4 đạt sáu checks,
bao gồm CUDA FP16/NMS: `outputs/tests/colab-cuda-smoke-20261002.txt`.

FFmpeg 4.4.2 trong runtime này không hỗ trợ `-fps_mode`. Extractor dùng
`-vsync 0` tương đương passthrough để giữ một JPEG cho mỗi frame được chọn,
không duplicate/drop sau select. Smoke thực trên Colab xác nhận 24 frames
của video 12 giây/25 FPS, PTS 0–11,52 và decoded ordinals 0–288 đúng bin
0,5 giây; 11 ingestion tests trên Windows vẫn đạt.
Evidence: `outputs/tests/colab-ffmpeg-compatibility-linux-20261002.txt`,
`outputs/tests/colab-ffmpeg-compatibility-windows-20261002.txt`.


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
khôi phục segment và stage sau runtime loss. Colab demo GPU→UI đã chạy thực;
benchmark cloud cùng sáu MOV đã đo ở mục benchmark phía dưới.

Integration GPU local chạy pipeline qua API của UI trên video test-pattern
12 giây, kiểm tra 24 sampled frames đến PTS 11,52, search sau kích hoạt và
vòng export/import artifact thật. Đây là kiểm thử transport local, không phải
chạy Colab. Log/evidence: `outputs/tests/fullvideo-workflow-log-final.txt`,
`outputs/tests/fullvideo-workflow-evidence.json`.

So sánh demo cùng video/scope/mode được ghi bên dưới; package/cache state
khác nhau, upload/download và initial model download chưa đo riêng.
Job overhead gồm unpack/startup/checkpoint, không phải isolated Drive I/O.
Quality M8 và máy thứ hai vẫn chưa nghiệm thu.

## Colab GPU → Windows UI: demo acceptance — 2026-10-02

Video test-pattern 12 giây, full-video/2 FPS, segment 5 giây, FP16/batch tối
đa 4; không phải traffic footage có nhãn. Tesla T4/runtime 2025.07,
Python 3.11.13, Torch 2.6.0+cu124; local baseline RTX 4070/Torch 2.7.1+cu126.
Commit executable `7523979`; raw SHA256 khớp local, 24 sampled frames
0–11,52 giây và decoded ordinals 0–288; 6 windows, 0 observations.

| Chỉ số | Local RTX 4070 | Colab T4 |
| --- | --- | --- |
| Tổng 12 stage timings | 33,687 s | 39,988 s |
| CLIP index, gồm stage startup | 16,109 s | 23,851 s |
| Tracking, gồm stage startup | 8,378 s | 8,343 s |

Colab job **43,542 s** gồm unpack, stages, checkpoint writes; batch gồm
result ZIP **45,204 s**. Ngoài stage là **3,554 s**, không tách riêng Drive I/O.
Lượt model setup thành công **20,460 s** dùng CLIP đã cache; không bao gồm
initial download/lượt setup thất bại. Upload/download chưa đo riêng.
Khác package/cache state, một lần đo; không kết luận T4 nhanh/chậm hơn cho
footage dài hoặc suy chất lượng từ thời gian.

Drive stage checkpoint có completed=12 và tracking checkpoint 36.724 bytes.
Rename run local để mô phỏng mất disk rồi chạy child process mới: restore
12 stages, không inference lại; wall restore/repack **4,054 s**, job **2,208 s**.
Result đầu giữ riêng; recovery không thay số runtime của phép đo đầu.

Nhập ZIP qua file chooser của UI Windows: activate run mới
`run-cloud-ecf034636631471e98fd186b24aa0728`; search 12 kết quả, source
readyState=4/seek 8/12 giây, overlay readyState=4/seek 10/12,02 giây.
Traffic hiển thị 6 windows và ROI pending; events không có ứng viên,
simulation là fixture riêng (1 assignment/2 unmet), không dispatch thật.
Report ZIP tải qua browser chứa 12 timings, cloud environment/runtime/
provenance và demo/M8 gates. 172 original files giữ SHA256/mtime;
chỉ dataset/job metadata đổi để activate. Không sửa approval.
Console không có lỗi; lưu hai network ERR_ABORTED khi đóng video/tải ZIP,
đồng thời playback và bytes ZIP đã được xác nhận.
Runtime đã disconnect/delete sau khi tải result; Drive checkpoints giữ lại.

Evidence: `outputs/tests/colab-acceptance-20261002-evidence.json`,
`colab-accepted-run-20261002.txt`, `colab-final-drive-recovery-20261002.txt`,
`colab-source-playback-20261002.json`, `colab-overlay-playback-20261002.json`,
`colab-ui-report-20261002.zip`, `colab-ui-console-20261002.json`.
11 ingestion tests và 11 cloud/workflow tests đạt sau portability fixes.
M8/ROI/labels/groups pending, quality metrics null, máy thứ hai NOT_RUN;
benchmark cloud sáu MOV đã đo riêng, không thay các review gates.

## Đo tracking trên sáu video CCTV thật — 2026-10-02

Chạy scope full video, 2 FPS trên sáu MOV của manifest hiện có, không sửa raw,
ROI hoặc outputs cũ. RTX 4070 Laptop, YOLOv8m FP16, batch tối đa 4,
ByteTrack reset theo segment 10 giây. Lệnh đã chạy từ repo:

```powershell
.\.venv\Scripts\python.exe -u -m pipeline.cctv.track `
  --manifest configs\cctv.demo.json `
  --traffic-config configs\cctv.traffic.demo.json `
  --model-config outputs\model-cache\yolo\yolo.json `
  --tracker-config configs\cctv.bytetrack.yaml `
  --output-dir outputs\demo-data\fullvideo-real-20261002 `
  --full-video --sample-interval-sec 0.5 --segment-seconds 10 --device cuda
```

| Chỉ số | Kết quả |
| --- | --- |
| Container duration tổng | 3.614,160 giây (60,24 phút) |
| Wall time tracking process | 2.043,97 giây (34,07 phút) |
| Sampled frames / segments | 7.232 / 365 |
| Observations / lỗi pipeline | 72.011 / 0 |
| Sampled frames / wall second | 3,54 |
| Container duration / wall time | 1,77× |
| PyTorch peak allocated / reserved | 183,82 / 284,00 MiB |
| Resume wall time | 5,49 giây |

Wall time gồm startup, source hashing, model setup, full-source decoding,
inference/tracking, SQLite checkpoints và publication. Không đo riêng từng
thành phần, không bật Drive ZIP checkpoint, không phải pipeline 12 stages.
Một lần đo, weights đã có local; so sánh native bổ sung ở mục phía dưới,
benchmark Colab cùng sáu MOV đã đo riêng ở mục benchmark phía dưới.
Observations không phải unique vehicles; memory không gồm toàn bộ CUDA driver.

Kiểm tra toàn ledger: timestamp khớp PTS/time base, locator tăng, sampled gap
tối đa 0,522 giây; sample cuối mỗi video cách container end không quá 0,75
giây. Sáu raw SHA256 khớp inventory và giữ mtime. Resume skip đủ 7.232 frames,
không load model, 371 artifacts giữ SHA256/mtime. Decoder phát 60.175 dòng
SEI truncation warnings; giữ log để điều tra, không coi exit code là bằng
chứng chất lượng hình ảnh hoặc tracking.

Evidence/environment/recipe/hashes:
`outputs/tests/fullvideo-real-20261002-evidence.json`.
Logs: `outputs/tests/fullvideo-real-20261002-console.txt` và
`outputs/tests/fullvideo-real-20261002-resume.txt`.
Không có quality metrics, approval ROI/M8, GPU Colab hoặc máy thứ hai mới.

## So sánh native mọi frame với 2 FPS — 2026-10-02

Đã chạy lại lệnh tracking phía trên với hai thay đổi:
`--sample-interval-sec 0` và output riêng
`--output-dir outputs\demo-data\fullvideo-native-real-20261002`.
Manifest/raw/config/model/code/environment giống run sampled; recipe chỉ
khác sample interval. Không thay outputs sampled hoặc dữ liệu review.

| Chỉ số | Native mọi frame | Sampled 2 FPS |
| --- | --- | --- |
| Container duration | 3.614,160 giây | 3.614,160 giây |
| Frames / segments | 90.352 / 365 | 7.232 / 365 |
| Wall time | 6.342,01 giây (105,70 phút) | 2.043,97 giây (34,07 phút) |
| Frames / wall second | 14,25 | 3,54 |
| Container duration / wall time | 0,57× | 1,77× |
| Observations | 1.823.973 | 72.011 |
| PyTorch peak allocated / reserved | 183,82 / 284,00 MiB | 183,82 / 284,00 MiB |
| Resume wall time | 9,05 giây | 5,49 giây |

Trong hai lần chạy tuần tự này, native mất 3,10× wall time; sampled giảm
67,77%. Không phải benchmark lặp có kiểm soát: desktop GPU users/load khác
nhau, chưa đo variance hoặc accuracy. Observation count khác nhau không
chứng minh số xe hoặc chất lượng tốt hơn; native và sampled không tương
đương về temporal coverage/association.

Native ledger có frame_idx liên tiếp từ 0 trên từng video, timestamp khớp
rational PTS, gap tối đa 0,042 giây. Decode độc lập sáu raw bằng PyAV khớp
90.352 frames và PTS cuối từng video, không chỉ dựa vào pipeline exit code.
Resume skip đủ frames, không load model; 371 native artifacts và 371
sampled artifacts giữ SHA256/mtime; sáu raw không đổi.
Decoder phát 60.197 dòng SEI truncation warnings trong native run; giữ log,
chưa có image-quality acceptance.

Evidence/environment/recipes/hashes:
`outputs/tests/fullvideo-native-real-20261002-evidence.json`.
Logs cùng prefix: `-console.txt`, `-resume.txt`, `-decode.txt`.
Đây vẫn chỉ là tracking local, không phải 12-stage pipeline hoặc đo cloud.
Benchmark Colab cùng sáu MOV đã đo riêng phía dưới; M8/ROI/labels/groups
pending, máy thứ hai NOT_RUN, quality metrics null.

## Benchmark Colab cùng sáu MOV — 2026-10-02

Chạy cả hai lệnh tracking trên sáu MOV nguyên SHA256/size, source archive
commit `9c9194f1c82bc5736e7081a2e651eaa04a61cba7`, cùng YOLOv8m weights,
Ultralytics 8.4.168, PyAV 16.1.0, lap 0.5.12, ByteTrack, FP16, batch tối đa
4 và segment 10 giây. Chỉ đổi sample interval 0,5/0 và output theo mode.
Cloud: Tesla T4, Python 3.11.13, Torch 2.6.0+cu124, CUDA 12.4, Linux.
Local: RTX 4070 Laptop, Python 3.11.9, Torch 2.7.1+cu126, CUDA 12.6, Windows.

| Chỉ số | Sampled 2 FPS | Native mọi frame |
| --- | --- | --- |
| Frames / segments | 7.232 / 365 | 90.352 / 365 |
| Local wall time | 2.043,974 giây | 6.342,012 giây |
| T4 wall time | 1.474,901 giây (24,58 phút) | 5.099,289 giây (84,99 phút) |
| T4 frames / wall second | 4,90 | 17,72 |
| Container duration / T4 wall time | 2,45× | 0,71× |
| Wall giảm so với run local | 27,84% | 19,60% |
| T4 observations | 72.011 | 1.824.927 |
| PyTorch peak allocated / reserved | 183,82 / 290,00 MiB | 183,82 / 290,00 MiB |
| Drive snapshot ZIP | 18,303 giây | 61,317 giây |
| Drive extract vào thư mục mới | 2,631 giây | 12,372 giây |
| Resume từ thư mục restored | 9,569 giây | 18,379 giây |
| Decoder SEI truncation warning lines | 60.175 | 60.197 |

Tracking wall gồm child process startup, source hashing, load model từ
weights cache, decode/inference/tracking, SQLite segment commits và
publication, cùng ranh giới CLI local. Không gồm upload, cài môi trường,
chuẩn bị raw hoặc ZIP snapshot/restore/resume. Không bật per-segment Drive
ZIP; hai snapshot sau khi từng mode hoàn tất được đo riêng.

GPU setup **346,670 giây** gồm cached YOLO restore/verify **3,652 giây**
và restore sáu raw từ Drive + SHA256 **43,547 giây**. Driver hash/copy raw
local thêm **12,277 giây**; validate raw cache Drive **15,297 giây**.
Các số setup có phần bao nhau, không cộng hai lần. Upload sáu raw không có
phép đo end-to-end hợp lệ: transfer/relay bị ngắt, runtime T4 đầu mất;
ba raw đầu khôi phục SHA256 từ Drive, ba raw còn lại gửi 17 phần tối đa
64 MiB trên CPU, assemble và verify trước khi cấp GPU mới. Không ngoại suy
tốc độ mạng từ thời gian chọn file hoặc một transfer attempt.

Hai child CLI exit 0, zero pipeline errors. Ledger trên từng video khớp
baseline local về frame count, PTS/time base, first/last locator và max gap;
native frame_idx liên tiếp. Mỗi mode có 371 artifacts: snapshot ZIP lên
Drive, extract vào thư mục mới, verify toàn bộ SHA256 rồi resume. Resume
skip toàn bộ frames, không load model, không sửa artifacts. Sáu raw vẫn
nguyên SHA256 sau cả hai run. GPU đã disconnect/delete sau khi tải evidence.

Ultralytics first-run settings text đứng trước JSON làm measurement wrapper
parse lỗi sau sampled CLI. Child đã hoàn tất; giữ nguyên wall time
1.474,901 giây và outputs, sửa parser của runner tạm, chạy lại phần
verification/snapshot/resume rồi chạy native. Không đo lại sampled inference.

Một run mỗi mode; khác OS/CPU, Torch/CUDA và load. Đây không phải hardware-only
benchmark, không có variance hoặc quality metrics. Native local/cloud có
1.823.973/1.824.927 observations: không phải unique vehicles hoặc bằng chứng
accuracy bằng nhau. Giữ decoder warnings, chưa nghiệm thu image quality.
Không chạy lại retrieval/congestion/events/dispatch; M8/ROI/labels/groups
pending, máy thứ hai NOT_RUN, thuyết trình người dùng chưa có bằng chứng mới.

Evidence: `outputs/tests/colab-six-video-benchmark-20261002.json`;
`outputs/tests/colab-six-video-evidence-20261002.zip` (631.057 bytes,
SHA256 `4878fc16a01d2d2e4b7488b4de6fe0a4a3e70857e5b7246255b00bbc31fbb7ef`).
ZIP gồm report/coverage/artifact hashes, stdout/stderr/resume logs, manifests,
track states, setup log và exact runners/inputs. Full mode snapshots nằm
trong Drive `MyDrive/CCTV_Colab/real-benchmark-20261002/{sampled,native}.zip`.

## Git

Các feature dùng Conventional Commits và đã/được push theo từng task.
Sau cập nhật từ repo trên máy khác:

```powershell
git pull --ff-only
.\scripts\Test-Windows.ps1
```
