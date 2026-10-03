# Đối chiếu kế hoạch và tiến độ CCTV


## M10 — hoàn tất runner hiệu chỉnh ngưỡng 27+9 và gỡ bottleneck kỹ thuật — 2026-10-03

Đã giải quyết hoàn toàn nút thắt cổ chai kỹ thuật (engineering bottleneck) cuối cùng của M10:

- **Triển khai runner hiệu chỉnh ngưỡng**: `pipeline/cctv/calibrate_thresholds.py` và CLI `scripts/calibrate_cctv_thresholds.py`
  thực thi đúng protocol đã chốt: sweep 27 cấu hình tắc đường (occupancy 0,20/0,25/0,30 × motion 8/12/16 px/s × persistence 4/6/8 s)
  và 9 cấu hình sự cố (stationary 0,02/0,04/0,06 × duration 1/2/3 s) trên tập `development`, tái sử dụng vết xe từ `tracks.db`.
- **Quy tắc chọn tối ưu và tie-breaking**: Ưu tiên F1 development tại IoU 0,3; hòa thì chọn FP/h thấp hơn; hòa tiếp thì chọn khoảng cách tương đối gần baseline hơn; hòa tiếp thì chọn theo thứ tự tham số tăng dần. Khi F1 undefined (chưa có nhãn/sự kiện), giữ nguyên baseline với ghi chú tường minh.
- **Đánh giá frozen test split**: Chạy bộ tham số tối ưu trên tập `test` tại các ngưỡng IoU 0,1; 0,3; 0,5 với đầy đủ phân rã theo nhãn và camera.
- **Bảo toàn tính toàn vẹn (Integrity)**: Khi dữ liệu nhãn là pending, runner trả về `NOT_EVALUATED_NO_REVIEWED_INTERVALS` với `operational_ready=False`, tuyệt đối không tự tạo nhãn giả.
- **Tích hợp và kiểm chứng hồi quy**: Đã tích hợp `test_cctv_calibration` vào `scripts/verify_cctv_t10.py` và `scripts/Test-Windows.ps1`.
  Bộ kiểm thử Windows đạt **358 tests: 357 passed, 1 skipped** (OS không cho directory symlink), pip check/compile/CUDA FP16/NMS đạt.
  Evidence: `outputs/tests/m10-calibration-core-tests.txt`, `outputs/tests/m11-regression.json`.
## M11 — cold UI pipeline đạt sau sửa ownership/commit — 2026-10-03

Đã tiếp tục blocker cold CLIP load, không thay bằng review/quality giả:

- Probe cũ giữ ~4.334 MiB private commit sau `ClipEncoder.close()`; child load
  lỗi Windows 1455 dù còn ~14,2 GiB physical RAM. `pread` cũng lỗi ở metadata map.
- Import NumPy/FAISS mặc định khiến coordinator không Torch giữ ~2.460 MiB
  private commit; đặt native pools 2 threads giảm còn ~215 MiB.
- CLIP phục vụ chuyển sang `ProcessClipEncoder`; close chờ process thoát trước
  stage GPU. Coordinator đọc Torch version bằng package metadata, không import
  Torch. Windows defaults `OPENBLAS_NUM_THREADS=2`, `OMP_NUM_THREADS=2`;
  explicit overrides giữ nguyên. Không đổi OS/pagefile, model, precision hoặc loader.
- Smoke CUDA: worker exit=0, commit headroom phục hồi ~4.109 MiB; loader trực tiếp
  tiếp theo đạt, text/image vectors có max error=0 so với process phục vụ.
- Fresh upload bằng file input browser: test-pattern H.264 12 giây, pending ROI,
  toàn video/2 FPS; run `run-3fe5e14f7773411b8a7fec00f222d87e` đạt **12/12**
  lần đầu, không retry hoặc index standalone. 24 index/tracking frames, 6 windows.
  Text/image UI mỗi loại trả 12 kết quả; source/overlay readyState=4, report button
  HTTP 200. ZIP 34 members/CRC đạt; uploaded source SHA/mtime giữ nguyên.
- 44 focused tests đạt. Full Windows verification: **353 tests, 352 passed/1 skipped**
  (OS không cho directory symlink); pip check/compile/CUDA FP16/NMS đạt.
  Evidence: `outputs/tests/m11-loader-before.json`, `m11-native-pool-commit.json`,
  `m11-process-lifecycle.json`, `m11-process-focused.txt`, `m11-process-core-tests.txt`,
  `m11-regression.json`, `m11-cold-smoke.json`, `m11-cold-report.zip`.
  Browser giữ hai ERR_ABORTED khi đóng video/download; không gọi đó là lỗi pipeline.

**M11 implementation + cold local smoke đạt trong phạm vi này**, không phải SLA
hoặc quality benchmark. Native mapping crash cũ phù hợp commit exhaustion
([PyTorch mapping source](https://github.com/pytorch/pytorch/blob/v2.7.1/aten/src/ATen/MapAllocator.cpp#L211-L231));
đây là inference cho crash cũ, không có native dump chứng minh từng lần fault.
Native file chooser OS chưa kiểm thử; file input/upload handler đã đi qua browser.
M9 vẫn complete demo cục bộ; M8 pending. T10.1–T10.6 chờ review/ROI/groups/splits/
calibration/quality thật, T10.7 physical host NOT_RUN, T10.8 cần người thuyết trình.

## Xử lý năm giới hạn kỹ thuật và chuẩn bị T10.7 — 2026-10-03

| Giới hạn | Kết quả đã quan sát | Không được suy thành |
| --- | --- | --- |
| Incident false alarms collective stop | 22 tests; smoke 3 ứng viên →0, isolated stop vẫn 1; queue-context có xét lại khi queue rời | Accuracy tai nạn; một xe chờ đèn đỏ vẫn confounder |
| Segment identity | 45 tests; optional geometric stitching six-MOV sampled: 11.453 tracklets →8.596 derived IDs, 2.857 links, 72.011 observations | Validated visual re-ID hoặc unique-vehicle/count quality |
| Speed | 52 traffic/overlay tests; synthetic 40 km/h, uncalibrated null; UI tách px/s và km/h unavailable | Km/h N001/N002: calibration surveyed vẫn pending, không tự bịa marker |
| Query Việt–Anh | 27 tests; 19-term expansion; CPU CLIP thật VI/EN cùng top-12, API hiển thị prompt | General translation hoặc relevance/Recall@K song ngữ |
| Dispatch scalability | 48 dispatch/resources/API tests; actual CLI 25 assignments/0 unmet; SciPy 1.17.1; 10×10 legacy parity | Mọi 100×100 scenario dưới 1 giây hay điều động thực tế |

Allocate 25×25 đo ba lần 0,891/0,404/0,412 giây, median 0,412 giây.
MILP hỗ trợ 100 team/100 demand, optimal zero-gap numerical certificate và
integer validation; không symbolic proof. Decimal-scaled travel có guard
`2**40`; thiếu SciPy/precision không an toàn chỉ fallback legacy có cảnh báo
khi cả team/demand ≤12, ngoài phạm vi hoặc certificate lỗi thì fail closed.
Evidence: `outputs/tests/limits-step2-tests.txt`,
`outputs/tests/limits-stitched-real-current/stitch_state.json`,
`outputs/tests/limits-step3-tests.txt`, `outputs/tests/limits-step4-tests-final.txt`,
`outputs/tests/limits-step5-tests-final.txt`, `outputs/tests/limits-milp-benchmark.json`.
Full core tại mốc năm giới hạn: 347 tests, 346 passed/1 skipped; counts/benchmarks bên dưới là lịch sử.

One-command `scripts/Test-SecondMachineHandover.ps1` nhận inventory gốc và
hostname origin, optional manifest/fresh output/expected commit; chạy Windows
dependency/CUDA FP16/NMS/compile/core verification và đóng gói nguồn SHA
trước/sau, snapshot/log/JSON/ZIP. [Handover](CCTV_HANDOVER.md) ghi cách transfer
và chạy. Local rehearsal đã đạt: **347 tests, 1 skip** (OS không cho tạo
directory symlink), pip check/compile/CUDA FP16/NMS đạt. ZIP kiểm tra đủ SHA
của 10 members; raw giữ hash/mtime và working-copy code giữ SHA trong lượt kiểm chứng.
Evidence: `outputs/limits-handover-verified/`; **không phải máy thứ hai**.
Same host giữ `NOT_RUN`; khác hostname chỉ ghi identity differs, technical pass
là `PENDING_HUMAN_VERIFICATION`, không chứng minh host vật lý độc lập.
Full-process pipeline/UI trên host vật lý khác vẫn cần người nghiệm thu.

M8/T10.4 cần hai reviewer thật; T10.2 cần ROI approval; T10.3 cần recording
groups/split verified; T10.5 cần measured calibration và development labels;
T10.6 cần independent quality evaluation; T10.7 cần host vật lý khác.
Forms/raw/review journals giữ nguyên, `quality_metrics=null`,
`physical_host_verified=false`, `operational_ready=false`, M8 `PENDING`.
Sealed dispatch-v1 giữ làm lịch sử; sau cutover dispatch-v2 phải regenerate
snapshot/publication vào output mới và trỏ demo store tới chúng, không sửa seal.
Ma trận/giới hạn chi tiết: [báo cáo hiện hành](CCTV_PROJECT_REPORT.md).

### Smoke full-process có recovery, không phải cold one-click pass

Browser upload H.264 test-pattern 12 giây, ROI pending, full video 2 FPS:
run `run-81fa3365fdf1491b830875d9bf5c3001` đạt 12/12 sau recovery;
24 index/tracking frames, 6 traffic windows, query Việt trả 12 kết quả và
prompt diễn giải. Overlay decode/seek 11 giây readyState=4; nút report trả
ZIP HTTP 200, CRC hợp lệ, 34 members. Source giữ SHA/mtime so với inventory.
Ba demo dispatch-v2 phục vụ đúng 1/2, 0/0 và review-gated 0/1 assignments/unmet.

**Cold run ban đầu chưa đạt:** stage CLIP load hai lần native access violation
`3221225477`, stack tại `torch/storage.py:466` khi Transformers load checkpoint.
Dừng owned server rồi index CUDA standalone đạt 24 vectors; restart server và
UI **Thử lại** hoàn tất các stages còn lại. Root cause chưa xác định; không
coi recovery này là sửa loader hoặc chứng minh cold one-click chạy ổn định.
Không đổi pagefile, CPU fallback hoặc review gates để che lỗi.
Evidence: `outputs/tests/limits-e2e-smoke.json`, `limits-e2e-report.zip` trong
cùng folder; console log giữ nguyên trong run. Owned server/browser đã dừng.


## Benchmark Colab sáu MOV hoàn tất — 2026-10-02

Đã đo tracking toàn bộ sáu raw MOV trên Tesla T4, cùng source identity,
YOLOv8m/ByteTrack, batch tối đa 4, FP16 và segment 10 giây như local:

| Mode | Frames / segments | RTX 4070 Laptop | Colab T4 |
| --- | --- | --- | --- |
| Sampled 2 FPS | 7.232 / 365 | 2.043,974 giây | 1.474,901 giây |
| Native mọi frame | 90.352 / 365 | 6.342,012 giây | 5.099,289 giây |

Hai run cloud không có pipeline errors; ledger khớp toàn bộ counts, PTS và
locator của baseline local. Mỗi mode có 371 artifacts: snapshot lên Drive,
extract vào thư mục mới và resume skip đủ frames, không load model; SHA256
artifacts không đổi. Sáu raw giữ SHA256. GPU đã disconnect/delete sau khi
tải evidence ZIP; không mua compute.

Đây là đo tracking, không phải pipeline 12 stages hoặc kiểm định quality.
Khác Torch/CUDA, OS/CPU và load; không kết luận chênh lệch do riêng GPU.
Transfer bị gián đoạn, không có số upload end-to-end hợp lệ; restore sáu
raw từ Drive và verify SHA256 đo riêng 43,547 giây. Setup GPU 346,670 giây
gồm restore đó; snapshot/restore/resume không nằm trong tracking wall time.
Ultralytics in settings trước JSON làm wrapper parse lỗi sau sampled run;
đã dùng timing/output giữ trong kernel để kiểm tra và tiếp tục, không chạy
lại sampled inference. SEI truncation warnings giữ nguyên trong logs.

Evidence: `outputs/tests/colab-six-video-benchmark-20261002.json` và
`outputs/tests/colab-six-video-evidence-20261002.zip`; chi tiết:
[Benchmark sáu MOV](CCTV_FULLVIDEO_COLAB.md#benchmark-colab-cùng-sáu-mov--2026-10-02).
T10.1–T10.6/M8 vẫn pending; T10.7 máy thứ hai NOT_RUN, T10.8 thuyết trình
người dùng chưa có bằng chứng mới. Không sửa forms hoặc tự tạo approval.

## Colab GPU → Windows UI: demo acceptance — 2026-10-02

Đã chạy job video test-pattern 12 giây trên Tesla T4, Python 3.11.13,
Torch 2.6.0+cu124: **12/12 stages**, 24 frames 2 FPS đến PTS 11,52,
6 traffic windows. Commit executable cuối: `7523979`.
Batch gồm result ZIP **45,204 giây**; job gồm stages/checkpoint **43,542 giây**.
Tổng stage local RTX 4070/T4: **33,687/39,988 giây**; index CLIP
**16,109/23,851**, tracking **8,378/8,343 giây**. Khác package/cache state;
không suy tốc độ sáu MOV hoặc chất lượng từ một demo ngắn.

Mất thư mục run local Colab rồi khôi phục Drive: skip cả 12 stages,
không chạy inference; restore/repack **4,054 giây**. UI Windows nhập ZIP
thật và activate run mới; search 12 kết quả, source seek 8 giây,
overlay seek 10 giây, 6 windows/ROI pending, events và simulation đã đọc.
Report ZIP tải qua browser có 12 stage timings, cloud environment/runtime/
provenance và `demo_only=true`, `operational_ready=false`, M8 PENDING.
172 original files, gồm raw, giữ SHA256/mtime; chỉ dataset/job metadata đổi
để activate. Console không có lỗi; giữ hai network ERR_ABORTED khi đóng
video và tải ZIP. GPU runtime đã disconnect/delete, không mua compute.

Đã sửa POSIX lease, notebook venv/CUDA shadowing, FFmpeg 4 passthrough và
source checksum LF/CRLF; 11 ingestion và 11 cloud/workflow tests đạt.
Evidence: `outputs/tests/colab-acceptance-20261002-evidence.json`;
chi tiết/giới hạn: [Toàn video và Colab](CCTV_FULLVIDEO_COLAB.md).
Benchmark sáu MOV cloud đã đo ở mục cập nhật phía trên; quality/M8,
review độc lập và máy thứ hai vẫn chưa nghiệm thu. Không tạo quality metrics.

## Kiểm tra plan và gates còn mở — 2026-10-02

Đối chiếu plan full-video/Colab và M10, không bổ sung benchmark local để
thay thế task cần đầu vào bên ngoài. Implementation/UI và phép đo tracking
local native/2 FPS đã hoàn tất trong phạm vi ghi bên dưới.

| Task còn mở | Gate kiểm tra thực tế | Hành động tiếp theo |
| --- | --- | --- |
| Benchmark Colab sáu MOV — đã đo | Sampled/native đủ 7.232/90.352 frames, 365 segments mỗi mode; snapshot/restore/resume đã kiểm tra | Xem phép đo và giới hạn ở mục benchmark; không ngoại suy quality |
| T10.1 content/groups | Sáu entries trong working content form vẫn pending, reviewer/group evidence null | Reviewer ghi bằng chứng nội dung/recording groups vào bản working |
| T10.2 ROI approval | N001/N002 pending, polygon/reviewer/time null | Người dùng review polygon trên nhiều thời điểm và ghi approval thật |
| T10.3 split | Sáu groups unverified, splits unassigned | Chỉ chia group-level khi grouping đã verified |
| T10.4/M8 | Giữ pending theo yêu cầu; chỉ một reviewer được xác nhận | Chờ hai notes blind độc lập, không tạo reviewer giả |
| T10.5–T10.6 calibration/quality | Chưa đủ labels/ROI/split gates | Không calibration hoặc chấm test bằng fixtures/demo pending |
| T10.7 máy thứ hai | Không có SSH hosts configured; chưa có quyền truy cập máy thứ hai được cung cấp | Cần máy Windows NVIDIA được truy cập hoặc checklist/log thực do người dùng chạy |
| T10.8 thuyết trình | Report và rehearsal kỹ thuật đã có, không có bằng chứng buổi thuyết trình mới | Người dùng diễn tập/thuyết trình theo kịch bản hiện có |

Colab T4/runtime 2025.07, quota và Drive mount đã xác nhận; demo job đã chạy
và import qua UI Windows. GitHub clone thiếu authentication trả 404; smoke
dùng source ZIP từ exact commit, SHA256 và 55 source identities đã đối chiếu.
Notebook: `notebooks/CCTV_Colab.ipynb`; route clone có authentication chưa
được kiểm chứng. Không mua compute; runtime đã giải phóng sau khi tải result.
Working forms: `outputs/evaluation/demo-review-working-v1`; không sửa sealed
templates hoặc tự điền approval. Quality/M8 vẫn pending, máy thứ hai NOT_RUN;
benchmark sáu MOV cloud đã đo; các task bị review gate vẫn chưa hoàn tất.

## Tiếp tục task pending: so sánh native và 2 FPS — 2026-10-02

Đã chạy native **mọi decoded frame của sáu MOV**, cùng config/model/code,
CUDA FP16, batch tối đa 4 và segment 10 giây như lần đo 2 FPS.
Output riêng: `outputs/demo-data/fullvideo-native-real-20261002`.
90.352 frames, 365 segments, 1.823.973 observations, 0 lỗi pipeline;
wall **6.342,01 giây (105,70 phút)** so với sampled 2.043,97 giây.
Trong hai lần đo này, native mất 3,10× wall time; sampled giảm 67,77%.
Không phải benchmark lặp có kiểm soát: desktop GPU load khác nhau,
không có nhãn để so sánh chất lượng hai mode.

Decode độc lập sáu raw bằng PyAV khớp frame counts và PTS cuối; ledger
frame_idx liên tiếp từ 0, timestamp khớp rational PTS, gap tối đa 0,042 giây.
Sáu raw giữ SHA256/mtime, 371 artifacts sampled giữ nguyên.
Resume native 9,05 giây, skip 90.352 frames, model_loaded=false;
371 native artifacts giữ SHA256/mtime. Peak PyTorch allocated/reserved
183,82/284,00 MiB. Decoder có 60.197 dòng SEI truncation warnings, giữ log.
Evidence: `outputs/tests/fullvideo-native-real-20261002-evidence.json`;
console/resume/decode logs cùng prefix. Chi tiết:
[Toàn video và Colab](CCTV_FULLVIDEO_COLAB.md#so-sánh-native-mọi-frame-với-2-fps--2026-10-02).
Local và Colab tracking hai mode đã đo toàn dataset; M8/ROI/labels/groups
và máy thứ hai vẫn chưa nghiệm thu. Không có quality metrics,
không chạy lại pipeline đầy đủ hoặc tự tạo approval.

## Tiếp tục task pending: đo tracking CCTV toàn video — 2026-10-02

Đã chạy tracking **toàn bộ sáu MOV thật**, 2 FPS, YOLOv8m CUDA FP16,
batch tối đa 4, segment 10 giây, output riêng
`outputs/demo-data/fullvideo-real-20261002`. Tổng container duration
3.614,160 giây; 7.232 sampled frames, 365 segments, 72.011 observations,
0 lỗi pipeline. Wall time **2.043,97 giây (34,07 phút)**, gồm startup/hash,
model setup, decode, inference/tracking, SQLite và publication; không có
Drive/ZIP checkpoint. Tỷ lệ duration/wall 1,77× chỉ là một lần đo tracking,
không phải tốc độ pipeline đầy đủ hoặc số xe thực tế.

Đối chiếu PTS/time base, locator tăng và khoảng sampled tối đa 0,522 giây;
sample cuối mỗi video cách container end không quá 0,75 giây.
Sáu raw giữ SHA256/mtime và khớp inventory. Resume 5,49 giây, skip 7.232
frames, model_loaded=false, 371 artifacts giữ SHA256/mtime.
PyTorch peak allocated/reserved 183,82/284,00 MiB, không phải tổng VRAM.
Decoder có 60.175 dòng cảnh báo SEI truncation; log giữ nguyên, không suy
ra chất lượng decode hoặc tracking đạt chỉ từ exit code.

Evidence: `outputs/tests/fullvideo-real-20261002-evidence.json`;
console/resume logs cùng prefix. Recipe và giới hạn:
[Toàn video và Colab](CCTV_FULLVIDEO_COLAB.md#đo-tracking-trên-sáu-video-cctv-thật--2026-10-02).
Không chạy lại retrieval, congestion, events hoặc dispatch.
Colab demo đã đạt; benchmark cloud sáu MOV đã đo ở mục cập nhật phía trên;
native local được đo trong lượt tiếp tục. M8/ROI/labels/groups vẫn pending,
máy thứ hai NOT_RUN; không tự tạo approval hoặc quality metrics.

## Cập nhật: toàn video, tốc độ và Colab — 2026-10-01

| Task trong plan mới | Trạng thái |
| --- | --- |
| Timing từng stage | Hoàn thành; có trong run và báo cáo ZIP qua UI |
| Toàn video/theo khoảng; 2 FPS/mọi frame | Hoàn thành, mặc định toàn video + 2 FPS |
| Batch YOLO và reuse preprocessing đã kiểm chứng | Hoàn thành; FP16, batch tối đa 4, OOM fallback |
| Export job/import result qua UI | Hoàn thành local; checksum, rebase và activation đã kiểm thử |
| Notebook Colab/Drive checkpoint | Đã tạo, kiểm tra cú pháp; stage/segment recovery đã test bằng fixture |
| So sánh GPU local/Colab | Tracking local/T4 cùng sáu MOV và hai mode đã đo; xem cập nhật 2026-10-02 |

Commits: `7f8fb3f`, `69a103e`, `373151d`, `b6095a3`, `a610758`.
279 core tests đạt, pip check/compile/CUDA FP16/NMS đạt; 61 tests focused sau
định dạng và 10 tests workflow/cloud cuối đạt. Node syntax/ROI drag đạt.
Integration GPU qua API UI: video test-pattern 12 giây, 24 sampled frames
đến PTS 11,52; pipeline đầy đủ, search và local export/import activation đạt.
Raw SHA/mtime giữ nguyên. Browser kiểm tra scope full/range và controls mới,
không có lỗi console; server kiểm thử 8088 đã dừng.

Ở lần cập nhật này chưa chạy GPU Colab hoặc CCTV dài thật; phép đo tracking
trên sáu MOV thật bổ sung ngày 2026-10-02 ở mục phía trên. Chưa có benchmark chất lượng mới.
Bạn cần đăng nhập/cấp quyền Drive, xác minh quota và chạy notebook. M8 giữ
PENDING, máy thứ hai NOT_RUN. Chi tiết thao tác, thiết kế, giới hạn và evidence:
[Toàn video và Colab](CCTV_FULLVIDEO_COLAB.md).

Ưu tiên hiện tại theo người dùng: **toàn bộ dữ liệu là demo; xử lý đầy đủ qua
một UI thân thiện**. M11 triển khai trong `CCTV_UI_WORKFLOW.md`: nhập demo/upload,
gán camera/ROI, tracking toàn video hoặc theo khoảng, pipeline tuần tự, tra cứu/evidence, review
demo, proposal mô phỏng, báo cáo ZIP và launcher Windows một click.
Smoke thực video mới đạt 12/12 stage; M8 và các gate chất lượng M10 vẫn pending.

| Task UI | Trạng thái | Kiểm chứng |
| --- | --- | --- |
| T11.1 Dataset demo/upload và camera | Complete | Upload video thật qua HTTP; giới hạn filename/size/origin có test |
| T11.2 ROI và tracking scope | Complete | UI lưu polygon pending; mặc định toàn video/2 FPS, khoảng 1–60 giây hoặc mọi frame |
| T11.3 Job, GPU tuần tự và resume | Complete | Smoke 12/12 stage; failure/retry và shutdown có test |
| T11.4 Mở kết quả vào các tab | Complete | Text query 12 kết quả; video seek giây 8; 5 traffic windows |
| T11.5 Review, proposal và export | Complete | Journal revision 1, pending gate 0/1, tải ZIP qua browser |
| T11.6 Launcher và hồi quy Windows | Complete | PowerShell parser, 264 core tests, CUDA FP16/NMS, Ruff/JS checks |

File chooser tự động qua Chrome extension chưa nghiệm thu do chưa có quyền
file URL; upload API đã kiểm thử. Chi tiết và giới hạn nằm trong hướng dẫn M11.

Ngày cập nhật: 2026-10-01. T3.5 tiếp nối traffic windows tại `8fdba1a`;
commit triển khai được ghi trong Git history.

## Cách đọc kế hoạch

ROI UI hỗ trợ giữ và kéo đỉnh bằng Pointer Events native, capture pointer,
tọa độ chuẩn hóa theo kích thước ảnh đang hiển thị và chặn sửa khi worker bận.
Kéo ngoài ảnh được clamp về biên; duplicate/cancel rollback, click sau drag
không thêm đỉnh. Thay đổi chỉ lưu khi bấm Lưu camera và ROI. Kiểm tra runnable:
`node scripts/test_cctv_roi.cjs` đạt; JS syntax đạt. Không thêm dependencies.

Sửa lỗi nhập ROI: UI bỏ qua double-click và đỉnh đã có trong phạm vi 6 pixel,
ghi rõ vùng tự đóng; backend giữ validator geometry và trả hướng dẫn tiếng
Việt thay stack Pydantic. Polygon lỗi không ghi đè preparation đã lưu.
Kiểm chứng: 12 workflow/traffic tests đạt, Node smoke duplicate/closing/
double-click đạt, JavaScript syntax đạt. Không inference hoặc sửa dữ liệu.

`KE_THUA_VA_PHAT_TRIEN_DO_AN.md`, mục 9, là lộ trình tám tuần cho video bài giảng.
Các yêu cầu môn học/OCR/ASR trong đó chưa phải checklist nghiệm thu CCTV.
Định hướng CCTV của người dùng thay thế phạm vi bài giảng, giữ kiến trúc kế thừa.
M0/M1/M2 đã được dùng trong tài liệu repo; M3–M6 và mã task dưới đây là kế hoạch
tiếp nối; trạng thái triển khai được cập nhật trong bảng dưới đây.

## Đối chiếu lộ trình gốc

| Mốc gốc | Bằng chứng hiện tại | Trạng thái trong phạm vi CCTV |
| --- | --- | --- |
| Tuần 1: tiếp nhận, kiểm kê, readiness | M0 inventory, runtime Windows, `/ready` của API CCTV 200 | Đạt cho API CCTV mới; serving bundle/API legacy chưa được nghiệm thu lại |
| Tuần 2: video mới qua pipeline | 6 MOV, 7.232 keyframes/maps, CLIP/FAISS/SQLite | Đạt nhánh hình ảnh; chưa chạy OCR/ASR trên CCTV |
| Tuần 3: chuẩn hóa pipeline | Manifest độc lập, output riêng, checkpoint/resume, hash guard | Đạt phạm vi pipeline CCTV hiện tại |
| Tuần 4: UI và vị trí video | Text/image query, xem MP4 tại timestamp, responsive | Đạt demo CCTV, không phải UI môn học |
| Tuần 5: cải tiến có baseline so sánh | Chưa có thực nghiệm so sánh | Chưa đạt |
| Tuần 6: benchmark có nhãn và split | Demo chưa có relevance/event labels độc lập | Chưa đạt |
| Tuần 7: chất lượng/latency/phân tích lỗi | Có bảng latency HTTP T6.2; chất lượng thiếu nhãn | Đạt phép đo kỹ thuật cục bộ; chất lượng chưa nghiệm thu |
| Tuần 8: máy thứ hai/báo cáo/bảo vệ | Hướng dẫn Windows đã có, chưa nghiệm thu máy thứ hai | Chưa đạt |

Không suy số tuần thực tế đã làm hoặc phần trăm hoàn thành từ bảng này.

## Milestones CCTV

| Milestone | Trạng thái | Bằng chứng hoặc điều kiện còn thiếu |
| --- | --- | --- |
| M0: legacy và Windows runtime | Hoàn tất nền | `.venv`, CUDA FP16/NMS, requirements và kiểm kê; full legacy suite vẫn có vấn đề được ghi riêng |
| M1: manifest và extraction | Hoàn tất demo | 6 video, 2 camera N001/N002, 7.232 frames; raw chỉ đọc, resume |
| M2: retrieval và demo API/UI | Hoàn tất chức năng | CLIP 768D, FAISS, SQLite read-only, text/image, video seek, readiness thật |
| M3: xe, tracking, congestion | Baseline demo có | T3.1–T3.5 chạy thử, ROI/timeline đã có trên API/UI; review ROI, hiệu chỉnh và benchmark còn mở |
| M4: tình huống bất thường/nghi tai nạn | T4.1–T4.2 có baseline CLI | Evidence/review và move-then-stop rule chạy thử; chưa có nhãn độc lập, calibration hoặc nhận diện tai nạn |
| M5: gợi ý phân bổ nhân lực | T5.1–T5.2 có baseline mô phỏng | Schema và solver bounded exact có assignments/unmet reasons; T7.2 bổ sung UI chỉ đọc, chưa có vị trí/nguồn lực thật |
| M6: đánh giá và bàn giao | T6.1–T6.3 có công cụ/tài liệu | Event evaluator, frozen HTTP latency, báo cáo và package bàn giao đã có; nhãn thật và chạy máy thứ hai còn mở |
| M7: tích hợp demo sự kiện/điều phối | T7.1–T7.3 đạt kỹ thuật cục bộ | Event/evidence, phân bổ mô phỏng, HTTP/browser smoke và runtime mới có; chưa nghiệm thu chất lượng hoặc máy thứ hai |
| M8: chuẩn bị review/nhãn độc lập | T8.1–T8.3 có công cụ pending | Tư liệu sạch, notes riêng, comparison và explicit adjudication/export; chưa review thật hoặc tạo ground truth |
| M9: tiện ích chạy thử | Hoàn tất T9.1–T9.6 | HTTP smoke, tracking overlay API/UI, runtime và rebuild đã kiểm chứng cục bộ; chất lượng/nhãn thật thuộc các mốc còn mở |
| M10: đánh giá độc lập và tái lập | Đang chuẩn bị T10.1 | Inventory sáu video và forms pending đã có; chỉ một reviewer, M8 pending theo user; ROI/groups/labels/máy thứ hai chưa đủ đầu vào |

Sau hai bản sửa launcher (encoding PowerShell và cổng bận), core suite đã chạy
lại bằng Windows PowerShell 5.1: **47 tests đạt**, runtime CUDA FP16/NMS đạt.
Có regression tests parser trên đĩa và kiểm tra cổng bận trước inference.
Kết quả này không đồng nghĩa full legacy suite đạt.
Sau T3.1, core suite đã chạy lại: **55 tests đạt** (thêm 8 tests traffic),
cùng runtime Windows CUDA FP16/NMS. CLI demo trả schema_valid=true,
roi_ready=false, pending N001/N002 như dự kiến.
Sau T3.2: **63 core tests đạt**, runtime CUDA FP16/NMS đạt. Detector đã xử lý đủ
7.232 keyframes và resume skip đủ, không nạp model/không rewrite artifacts.
Xem `CCTV_DETECTION.md`; chưa có benchmark detector hoặc sự kiện.
Sau T3.3: **74 core tests đạt**, runtime CUDA FP16/NMS đạt. Tracking từ video
gốc chạy 6 clip 10 giây, 1.501 frame/12 segment; join 120 timestamp keyframe
cũ, resume skip đủ không nạp model và không rewrite 18 artifacts/state/log.
Xem `CCTV_TRACKING.md`; chưa nghiệm thu tracking toàn video hoặc chất lượng ID.
Sau T3.4: **92 core tests đạt**, runtime CUDA FP16/NMS đạt. Chế độ draft chạy
30 cửa sổ từ 6 clip/1.501 frame, resume skip đủ và không rewrite 6 artifacts/
state/log. Có 6 ảnh ROI nháp, ROI vẫn pending và ngưỡng chưa hiệu chỉnh.
Xem `CCTV_CONGESTION.md`; không coi kết quả demo là benchmark congestion.
Sau T3.5: **100 core tests đạt**, CUDA FP16/NMS đạt. API thật phục vụ 30 windows/
6 ROI images; trình duyệt lọc camera/video, chọn timeline và seek 34.001 giây
đến frame 850 đúng N002-V003. Mobile 390×844 không tràn ngang toàn trang.
Traffic readiness độc lập retrieval, giữ cờ draft/uncalibrated. Xem `CCTV_TRAFFIC_UI.md`.
Sau T4.1: **116 core tests đạt**, CUDA FP16/NMS đạt. Package sáu clip có
0 ứng viên từ traffic rule; package tổng hợp riêng có 1 `demo_case`, tham
chiếu frame thật N001-V001 751/30.037 giây và 900/35.996 giây. CLI review
kiểm thử ba revision trong journal demo, giữ scope demo và flags gốc.
Evidence/schema/hash, checkpoint/resume, bảo vệ input, CAS, reopen và audit
chain đã kiểm thử. Chưa có nhận diện tai nạn hoặc event UI. Xem `CCTV_EVENTS.md`.
Sau T4.2: **128 core tests đạt**, CUDA FP16/NMS đạt. Move-then-stop screening
chạy CPU trên 6 clip/1.501 frame, tạo 0 ứng viên và resume giữ nguyên output.
10 quỹ đạo tổng hợp đạt kỳ vọng rule, gồm chờ đèn đỏ cũng tạo ứng viên để
minh họa false alarm. Event v2 giữ scope track/segment và evidence/PTS; review
v1/v2 tương thích, raw và mọi upstream publications giữ nguyên. Xem
`CCTV_INCIDENTS.md`, kế hoạch nhãn/split/metrics trong `CCTV_EVENT_EVALUATION.md`.
Sau T5.1: **141 core tests đạt**, CUDA FP16/NMS đạt. Resource simulation có
2 zone/3 team, roster=6, available=4, reserve=2 nên dispatchable cap=2.
Schema kiểm tra canonical camera bindings, unknown=null, directed travel,
team skills/availability/allowed zones, budget và indivisible teams. Report
resume giữ nguyên hash/mtime; raw và mọi upstream artifacts giữ nguyên.
Chưa tạo assignments hoặc gợi ý điều động. Xem `CCTV_RESOURCES.md`.
Sau T5.2: **155 core tests đạt**, CUDA FP16/NMS đạt. Exact simulation solver
gán SIM-TEAM-A (2 người) cho demand priority 4, giữ reserve=2; hai demand còn
lại bị chặn bởi review gate/shared cap. Brute-force oracle độc lập kiểm tra
objective; cap, whole teams, skills, directed travel và mỗi team một lần đã
kiểm thử. Event package rỗng không tạo assignment; demo case needs_more_evidence
được giữ lại, journal không sửa. Ba package resume giữ nguyên bytes/mtime,
raw và upstream artifacts giữ nguyên. Xem `CCTV_DISPATCH.md`.
Sau T6.1: **171 core tests đạt**, CUDA FP16/NMS đạt. Annotation validator,
video/recording-group splits và camera holdout checks; max-total-IoU exact
matching và sensitivity 0.1/0.3/0.5, TP/FP/FN/precision/recall/F1, reviewed
camera-hours và confirmation-PTS delay đã có. Fixture riêng TP=1/FP=2/FN=1
đối chiếu oracle. Sáu clip thật giữ pending/unassigned; CLI trả
NOT_EVALUATED_NO_REVIEWED_INTERVALS, metrics=null. Resume giữ bytes/mtime,
không đổi nhãn/raw/upstream packages. Xem `CCTV_BENCHMARK.md`.
Chi tiết và bằng chứng cục bộ: `CCTV_DEMO_UI.md`, `CCTV_RETRIEVAL.md`,
`outputs/demo-data-review/ui-verification.json`.

Sau T6.2: **182 core tests đạt**, CUDA FP16/NMS và pip check đạt. Workload
12 query, top-k=12, 24 warmup/120 request đo qua HTTP; lỗi=0. Text P50/P95
52,66/56,43 ms; image 127,91/141,34 ms. Client không tải model, server một
CLIP FP16; allocator peak trong phiên model allocated=843,79 MiB,
reserved=862 MiB, không phải tổng VRAM hoặc peak riêng request. Frozen input,
index/model/code/session guards, case checkpoint và no-network completed
resume đã kiểm tra. Raw 6 file, nhãn pending và upstream artifacts giữ nguyên
hash/mtime. Chưa có relevance labels; quality metrics=null. Xem
`CCTV_RUNTIME_BENCHMARK.md`, `outputs/demo-data/runtime-benchmark-v1/runtime_report.json`
và `outputs/demo-data-review/runtime-benchmark-verification.json`.

Sau T6.3: **189 core tests đạt**, CUDA FP16/NMS và pip check đạt. Báo cáo
tổng hợp `CCTV_PROJECT_REPORT.md`, quy trình rebuild theo stage bằng PowerShell
và exporter `scripts/export_cctv_handover.py` đã có. Package
`outputs/demo-data/handover-v1` đối chiếu ba sealed reports, source manifest,
checksum/protected inputs; resume giữ hash/mtime ba JSON. Template máy thứ
hai có 11 checks pending, trạng thái NOT_RUN; không sao chép raw/model hoặc
tự tạo kết quả PASS. Chất lượng retrieval/event vẫn chưa có nhãn độc lập.
Xem `CCTV_HANDOVER.md` và `outputs/demo-data-review/handover-verification.json`.

Sau T7.1: **194 core tests đạt**, CUDA FP16/NMS và pip check đạt. API event
v1/v2 chỉ đọc, source/ledger/journal binding, live review state/revision và
video evidence seek đã có. Tab phân biệt package unavailable, 0 candidates
và case synthetic. Không ghi review, không nạp model thứ hai. Benchmark T6.2
giữ như phép đo lịch sử, code mới cần output đo mới. Xem `CCTV_EVENTS_UI.md`;
dispatch UI tiếp nối T7.2.
Browser mở evidence đúng 30,037/35,996 giây, giữ review revision 3; raw,
event/journal và upstream artifacts giữ hash/mtime. Bằng chứng:
`outputs/demo-data-review/events-api-verification.json`.

Sau T7.2: **202 core tests đạt**, pip check, compile và CUDA FP16/NMS đạt.
API/UI chỉ đọc ba báo cáo mô phỏng: fixture 1 assignment/2 unmet; event rỗng
0/0; demo review gate 0/1, `needs_more_evidence` revision 3. Kiểm tra hiện tại
nguồn/config/journal, cap/reserve và khả thi; snapshot stale trả unavailable,
không chạy solver hoặc nạp model khác. HTTP kiểm tra 68 artifacts/configs giữ
hash/mtime; browser ba kịch bản, Home/End và console không lỗi. Xem
`CCTV_DISPATCH_UI.md` và `outputs/demo-data-review/dispatch-ui-verification.json`.
Benchmark T6.2 vẫn là lịch sử; T7.3 đo lại vào output mới với code hiện tại.

Sau T7.3: **208 core tests đạt**, pip check, compile và CUDA FP16/NMS đạt.
CLI `scripts/verify_cctv_demo.py` kiểm tra UI assets, text/image locator, ảnh,
video Range, 30 windows, events và ba dispatch packages, không ghi review.
Browser kiểm tra search video tại 488,014 giây, traffic evidence 30,037 giây,
event evidence 35,996 giây, review gate và console không lỗi. Benchmark mới:
120 request/24 warmup, lỗi=0; text P50/P95=49,76/53,78 ms, image
147,91/167,64 ms. Hai report/state giữ hash/mtime khi resume. Xem
`CCTV_DEMO_ACCEPTANCE.md`; report local dưới `acceptance-t7-v2` và
`runtime-benchmark-t7-v1`. Chất lượng, ROI/nhãn và máy thứ hai còn mở.

Sau T8.1: **214 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt.
Bundle `review-preparation-v1` có sáu khoảng `[30,40)`, 36 JPEG nguyên bản,
sáu contact sheets và sáu ROI overlays riêng. Trang labeling không có ROI
draft/predictions, dùng video preview hiện có/source PTS; forms giữ pending,
0 reviewed/approved, group unverified và split unassigned. Resume skip sáu
interval, 55 artifacts giữ SHA/mtime, 59 input files được freeze. Browser
tải sáu sheets, video seek 30,000 giây và console không lỗi; sáu raw cùng
upstream không đổi. Bộ này không chứng minh blind review/tính đại diện.
Xem `CCTV_REVIEW_PREPARATION.md`; notes/nhãn thật chưa được cung cấp.

## Task tiếp theo và các bước có thể commit riêng

**T3.1–T3.5 baseline đã hoàn tất trong phạm vi được ghi; ROI thật vẫn chưa
duyệt và ngưỡng chưa hiệu chỉnh. T4.1 evidence/review đã hoàn tất trong phạm vi
CLI. T4.2 có baseline rule/fixtures và evaluation protocol; chưa nghiệm thu
nhận diện tai nạn. T5.1 đã có schema khu vực/nguồn lực mô phỏng.
T5.2 có MILP số học certified đến 100 team/100 demand, warned legacy fallback
chỉ khi cả hai ≤12; toàn bộ nguồn lực vẫn mô phỏng.
T6.1 evaluator và T6.2 frozen runtime benchmark đã có; nhãn độc lập và
recording grouping vẫn chưa xác nhận.
T6.3 đã có gói bàn giao/báo cáo và checklist. Bước nghiệm thu tiếp theo:
chạy trên máy thứ hai theo `CCTV_HANDOVER.md`, lưu commit/log/kết quả thực;
review ROI và tạo nhãn độc lập vẫn còn mở. T7.1 đã có UI event/evidence chỉ
đọc; T7.2 đã nối phân bổ mô phỏng lên UI và T7.3 nghiệm thu kỹ thuật cục bộ.
T8.1 đã chuẩn bị bộ mẫu pending, T8.2 có notes riêng và validation, T8.3
có quyết định adjudication/export sang schema T6.1. Bước nghiệm thu tiếp
theo cần review/adjudication thực, xác minh groups/splits và ROI; không tự
phê duyệt hoặc tạo nhãn thật. Chạy máy thứ hai vẫn cần môi trường
thứ hai thực tế.** Xem `CCTV_TRAFFIC.md` để
kiểm tra ROI readiness. Tracking cần frame liên tiếp và PTS thật; detections
keyframes 2 FPS hiện tại chưa chứng minh tracking hoặc lưu lượng hoạt động tốt.
Chưa sửa model/index CLIP hiện có. Tạo schema/output độc lập cho detections,
tracks và traffic windows, giữ camera/video/timestamp/frame_idx để liên kết
keyframe. ROI đường dùng tọa độ chuẩn hóa và cần duyệt trên ảnh từng camera;
không tự suy vị trí, thời gian tuyệt đối hoặc hiệu chuẩn tốc độ từ tên video.

| Task đề xuất | Kết quả nhỏ có thể kiểm thử và push |
| --- | --- |
| T3.1: schema/ROI — đã có | Validate ROI và locator; kiểm thử cấu hình hợp lệ/lỗi; N001/N002 chưa duyệt |
| T3.2: phát hiện xe — đã có | FP16, batch giới hạn, checkpoint và sáu previews; đủ 7.232 frames, resume/hash/VRAM đã kiểm tra |
| T3.3: tracking — đã có baseline và optional stitching | Native frames/PTS, scoped segment IDs/checkpoint; derived geometric IDs tùy chọn, không validated visual re-ID/count quality |
| T3.4: congestion/speed — đã có kỹ thuật | Count/union occupancy, px/s, coverage/persistence; surveyed homography tùy chọn cho km/h, N001/N002 pending/null, ROI/threshold quality chưa hiệu chỉnh |
| T3.5: API/UI giao thông — đã có baseline | ROI, timeline/evidence/overlay; UI tách px/s và km/h unavailable khi không calibration |
| T4.1: event evidence/review — đã có nền CLI | Schema ứng viên/demo, frame/PTS thật, package immutable, video checkpoint, journal review riêng và CAS; 0 ứng viên dữ liệu hiện có, 1 case tổng hợp để test |
| T4.2: nghi sự cố — có queue context | Move-then-stop scoped tracks/PTS, lọc collective stop/queue và xét lại; xe đơn lẻ đèn đỏ còn confounder, footage quality pending |
| T5.1: nguồn lực — đã có cấu hình mô phỏng | Zone/team/skills/allowed zones, availability, directed travel, budget/reserve; schema và report/resume chạy thử, không gán vị trí thật |
| T5.2: gợi ý phân bổ — có MILP CLI | 100×100 numerical certified priority/travel/staff/tie-break; guards/fail closed, warned fallback cả hai ≤12, cap/reserve/skills/review gates giữ nguyên; không điều động |
| T6.1: annotation/splits/evaluator — đã có công cụ | Source-bound labels, review/coverage validation, group/camera leakage checks, exact weighted IoU matching, metrics và fixtures; chưa có nhãn thật để chấm chất lượng |
| T6.2: runtime benchmark — đã có kỹ thuật | Frozen workload/input/model/index/code, 120 HTTP samples, P50/P95 và lỗi, case checkpoint; relevance/event quality thiếu nhãn |
| T6.3: bàn giao — tooling đã smoke local | One-command Windows/source/environment package đạt; 347 tests/1 skip, ZIP SHA verified; physical second-machine NOT_RUN |
| T7.1: event UI — đã có chỉ đọc | Event/review state, source guards, synthetic/empty/unavailable states và video evidence; không ghi review |
| T7.2: phân bổ UI — đã có chỉ đọc | Đọc simulation report, cap/reserve, assignments/unmet reasons; cấu hình/journal đổi thì snapshot cũ không khả dụng |
| T7.3: nghiệm thu demo — đạt kỹ thuật cục bộ | HTTP/browser smoke và latency mới đã có; giữ benchmark lịch sử, chưa có nhãn thật/máy thứ hai |
| T8.1: chuẩn bị review — đã có | 36 ảnh sạch, sáu sheets/ROI drafts và forms pending; checkpoint/resume, không đọc predictions |
| T8.2: notes/validation — đã có | Hai templates riêng, scope/time/provenance và exact multiset comparison; sáu khoảng demo vẫn pending, không xuất ground truth |
| T8.3: adjudication/export — đã có | Quyết định rõ ràng, notes SHA/coverage/blind/acceptance/time gates, chuyển schema T6.1; demo vẫn sáu khoảng pending |
| T9.1: managed demo smoke — đã có | Một lệnh PowerShell, socket reservation, readiness timeout, HTTP T7.3 và shutdown server sở hữu; existing mode không tạo/dừng server |
| T9.2: tracking overlay — đã có offline | MP4 H.264 CPU, bbox/segment-scoped ID từ ledger, source/PTS guards, checkpoint/resume; không inference mới hoặc duyệt ROI |
| T9.3: overlay trên traffic UI — đã có | MP4/Range và optional catalog, provenance/snapshot guards, source offset, nút xem clip; không duyệt dữ liệu |
| T9.4: runtime/bàn giao hiện tại — đã có | 120 measured + 24 warmup không lỗi, resume không HTTP, sealed handover; máy thứ hai NOT_RUN |
| T9.5: hoàn thiện hướng dẫn rebuild — đã có | Bổ sung ROI previews, events, overlay và dispatch snapshots; kiểm chứng CLI/resume và pending review không gán đội |
| T9.6: smoke cho bundle dựng mới — đã có | Bỏ phụ thuộc runtime lịch sử/journal bắt buộc, bảo vệ request và journal có/không có; regression và HTTP smoke đạt |

Sau T8.2: **225 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt.
11 tests mới dùng fixtures; hai templates và comparison demo giữ sáu khoảng
pending, không tạo ground truth. Hướng dẫn: `CCTV_REVIEW_NOTES.md`.

Sau T8.3: **235 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt.
10 tests mới kiểm tra adjudication bằng fixtures. Export demo giữ sáu khoảng
pending; evaluator T6.1 đọc được và trả NOT_EVALUATED_NO_REVIEWED_INTERVALS,
metrics null. Resume giữ SHA/mtime 69 files nguồn/tư liệu/artifacts kiểm tra,
sáu video raw và upstream packages không đổi. Hướng dẫn: `CCTV_ADJUDICATION.md`.

T9.1 bổ sung tiện ích theo mục tiêu chạy thử hiện tại: **244 core tests đạt**,
pip check/compile/CUDA FP16/NMS đạt; managed HTTP smoke trên 8087 passed,
100 protected files giữ SHA/mtime và socket giải phóng sau shutdown.
Xem `CCTV_DEMO_SMOKE.md`; nhãn/ROI/máy thứ hai vẫn chưa nghiệm thu.

T9.2: **252 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt; sáu MP4
overlay CPU có 1.501 frames, PTS output đối chiếu đầy đủ. Resume skip sáu
video, 26 files giữ SHA/mtime; raw/tracking/upstream giữ nguyên.
Xem `CCTV_TRACKING_OVERLAY.md`. T9.3 đã nối overlay vào UI traffic:
**259 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt; HTTP smoke
RequireOverlays đạt sáu clips, 108 protected files giữ SHA/mtime. Browser
source 36,036 → overlay 5,999 giây, readyState=4, console không lỗi.
Xem `CCTV_OVERLAY_UI.md`; ROI/nhãn thật và máy thứ hai vẫn pending.

T9.4: benchmark app `9ff3006` đạt; text P50/P95=52,18/56,18 ms,
image=128,07/139,86 ms, không lỗi trong 120 measured + 24 warmup requests.
Resume không HTTP; 56 protected files và hai runtime JSON giữ SHA/mtime.
Gói `handover-t9-v1` export/resume ba JSON đạt; máy thứ hai NOT_RUN,
event metrics null. Không đổi executable code, giữ bằng chứng 259 tests
T9.3; xem `CCTV_RUNTIME_BENCHMARK.md` và `CCTV_HANDOVER.md`.

T9.5 sửa hướng dẫn handover/README còn thiếu assets hoặc mô tả UI cũ.
Sáu CLI commands resume đạt, 76 protected files giữ SHA/mtime. Rehearsal
CPU riêng với journal chưa có giữ pending_review: 0 assignments/1 unmet,
không tạo journal. Không đổi executable code, không nạp model; máy thứ hai
vẫn NOT_RUN. Xem `CCTV_HANDOVER.md`, mục T9.5.

T9.6 sửa dependency không cần thiết trong verifier dùng chung cho CLI và
managed/existing smoke. Không cần report runtime lịch sử hoặc journal đã
review; journal absent phải giữ absent trong run, request dispatch được
freeze. Regression kiểm tra thiếu cả ba journals/report và reject journal
xuất hiện giữa run. Managed CUDA smoke đạt sáu overlays/30 windows,
107 protected files giữ SHA/mtime, owned server dừng và cổng 8087 giải phóng.
Report: `outputs/demo-data/demo-smoke-20261001-171142-10994a76/acceptance_report.json`.
Không sửa raw/review/ROI hoặc logic inference/dispatch. Các mốc còn cần
đầu vào thực vẫn là review/labels/groups/ROI và máy thứ hai; không bổ sung
module mới chỉ để tăng số task.
Sau T9.6: **260 core tests đạt**, pip check/compile/CUDA FP16/NMS đạt.

## Đóng milestone M9 — 2026-10-01

Toàn bộ task T9 đã hoàn tất trong phạm vi chạy thử demo cục bộ:

| Task | Commit triển khai | Kết quả nghiệm thu |
| --- | --- | --- |
| T9.1 | `23ffa86` | Managed HTTP smoke và shutdown server sở hữu |
| T9.2 | `6efa11a` | Sáu MP4 overlay CPU, 1.501 frames/PTS verify và resume |
| T9.3 | `9ff3006` | Overlay API/Range, traffic UI và browser source-offset playback |
| T9.4 | `6138cfb` | 120 measured + 24 warmup không lỗi, runtime/handover resume |
| T9.5 | `ecb9cc1` | Hướng dẫn rebuild assets, CLI/PowerShell và pending dispatch |
| T9.6 | `ba35139` | Smoke không cần journals/report lịch sử; 260 core tests đạt |

Đối chiếu cuối: report smoke T9.6 passed, sáu overlays/30 windows;
SHA256 của 107 protected files trong report vẫn khớp files hiện tại.
Không thay code sau bộ kiểm thử này; không cần thêm task T9 hoặc chạy lại
inference để đóng milestone. Lệnh chạy lại demo:

```powershell
Set-Location 'D:\Project ExploreCS\CCTV_keyframes_retrieving'
.\scripts\Test-CctvDemo.ps1 -RequireOverlays
```

M9 complete không đồng nghĩa toàn bộ đồ án đã nghiệm thu: các điều kiện
review/labels/groups/ROI, chất lượng retrieval/events và máy thứ hai vẫn
thuộc M3/M4/M6/M8; vị trí/nguồn lực thật của M5 chưa có. Giữ pending,
không tự tạo phê duyệt hoặc kết quả thực nghiệm để đóng các mốc đó.

```powershell
git diff --check
git add docs/CCTV_PROGRESS.md
git commit -m 'docs: close T9 demo milestone with verified acceptance evidence'
git push origin main
```

## M10 — triển khai theo kế hoạch đánh giá

### Cập nhật tự động hóa demo T10 — 2026-10-01

Đã triển khai tuyến **Tự kiểm chứng T10** qua UI, dùng lại run hoàn tất:
inventory, ROI/schema, evaluator fixture, CPU rules/solver/workflow tests,
HTTP smoke. Kiểm chứng có job/checkpoint riêng, dùng chung một worker với
pipeline, hỗ trợ dừng/retry và JSON/Markdown trong ZIP. Không inference lại
hoặc tự phê duyệt nội dung/nhãn/ROI. Pipeline run đổi làm kết quả cũ stale.

Smoke thực trên dataset **M11 browser smoke**: năm bước đạt, 46 CPU tests
trong worker đạt. Browser đã kiểm thử Dừng → Thử lại T10 cùng job ID,
hoàn tất 5/5 và tải ZIP có `t10/report.json`/`report.md`/logs.
Regression Windows: **268 core tests đạt** trong 90,089 giây; pip check,
compile, CUDA FP16/NMS và `node --check static/cctv/workflow.js` đạt.
Nguồn pipeline/raw được verifier kiểm tra SHA/mtime trước và sau.

Trạng thái kỹ thuật: `PASSED_DEMO_CHECKS`, `quality_metrics=null`.
Trạng thái độc lập: T10.1 content/groups, T10.2 ROI approval, T10.3 split,
T10.5 calibration, T10.6 chất lượng vẫn pending; T10.4/M8 PENDING;
T10.7 máy thứ hai NOT_RUN. T10.8 có báo cáo kỹ thuật tự sinh; rehearsal kỹ
thuật UI đã hoàn tất như cập nhật bên dưới, chưa thuyết trình người dùng.
Chi tiết thao tác: [UI workflow](CCTV_UI_WORKFLOW.md); gates đánh giá:
[M10](CCTV_M10_EVALUATION.md). Log regression: `outputs/tests/core-tests.txt`.

Chi tiết protocol/tasks/gates và PowerShell: `CCTV_M10_EVALUATION.md`.
T10.1 technical inventory đã chạy trên sáu MOV hiện có, 3.614,160 giây
container duration, same raw SHA như inventory gốc. Năm JSON resume giữ
SHA/mtime; content/groups/splits/ROI không tự suy hoặc duyệt. Kiểm kê kỹ
thuật hoàn tất không đồng nghĩa content/group verification hoàn tất.
Theo cập nhật người dùng: chỉ một reviewer, giữ M8 pending và chờ thông tin.
T10.2–T10.6 chờ ROI/groups/nhãn đủ gate; không chạy calibration trên demo
pending. T10.7 chưa có máy thứ hai được truy cập/chạy; vẫn NOT_RUN.
T10.8 đã cập nhật báo cáo đến M11 và chuẩn bị kịch bản bảo vệ demo 10 phút/Q&A;
checklist T10.7 bổ sung tuyến UI sau setup. Đối chiếu artifacts/log hiện có,
không chạy lại inference cho thay đổi tài liệu. Chưa có thuyết trình/bảo vệ,
quality metrics hoặc kết quả máy thứ hai mới; các phần này vẫn pending.

### T10.8 — rehearsal kỹ thuật UI hoàn tất

Ngày 2026-10-01 trên commit `b44120a`, dùng lại M11 browser smoke. Worker
T10 mới đạt 5/5; browser kiểm tra 12 kết quả text, source seek 10,52 giây/
duration 12, overlay duration 10, readyState 4, 5 traffic windows, ROI pending.
Review giữ needs_more_evidence revision 1; simulation 1 assignment/2 unmet,
review gate 0/1. ZIP từ UI có T10 JSON/Markdown; console không lỗi,
132 protected source/code/output files giữ SHA256 sau rehearsal.
Evidence: `outputs/tests/t10-rehearsal.json`; report tại
`outputs/ui-workflow/3c2df27735c74e33b9c289d858e560a2/t10-e48f46da019a4e11b710cc3879c9b354/report.json`.
Owned server đã dừng sau rehearsal. Không chạy lại inference, không thay
review hoặc dùng fixture như nhãn thật. Core suite 268 tests vẫn là bằng
chứng regression đã đạt; thay đổi lần này chỉ là tài liệu.

Phần demo tự động của T10 hoàn tất. Còn người dùng diễn tập thuyết trình;
T10.1–T10.6 độc lập chờ đầu vào, M8 PENDING; T10.7 máy thứ hai NOT_RUN.
Kiểm chứng bước chuẩn bị: **261 core tests đạt**, pip check/compile/CUDA
FP16/NMS đạt; bản sao ba forms working đã tạo mà không phê duyệt hoặc gán nhãn.

Mỗi task: code/module hoàn chỉnh → kiểm thử phù hợp và dữ liệu mẫu → tài liệu
PowerShell → Conventional Commit → push. Chạy CLIP phục vụ và detector theo
stage riêng để tránh giữ hai model lớn đồng thời trên GPU 8GB.

Hiện chưa có nhãn tai nạn/tắc đường, ROI, vị trí camera hoặc nguồn lực thực tế.
Các thiếu hụt này không cản việc tạo schema và detector baseline, nhưng chưa đủ
để nghiệm thu chất lượng sự kiện hoặc khuyến nghị điều phối thực tế.
