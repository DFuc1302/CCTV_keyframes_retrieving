# Kế hoạch đánh giá sự kiện độc lập

Trạng thái: **protocol đã có evaluator T6.1, chưa có nhãn hoặc benchmark thật**.
Xem [`CCTV_BENCHMARK.md`](CCTV_BENCHMARK.md) để chạy validator, split và metrics.
Fixture
T4.2 kiểm tra logic code; reviewer journal là quyết định vận hành, không được
tự dùng làm ground truth nếu người review chỉ xem những event rule đã tạo.

## 1. Thu thập và annotation

Chọn cả đoạn có sự cố và không có sự cố, gồm: xe chờ đèn đỏ, hàng xe, xe đỗ,
xe đi chậm, che khuất, camera rung, thiếu sáng và track ID switch. Giữ raw
read-only; lưu annotations riêng. Hai người xem video liên tục độc lập với
output rule, ghi camera/video, thời gian PTS tương đối và interval được xem.
Tranh chấp cần adjudication; vùng không quan sát rõ có nhãn unknown.

Tách ba khái niệm: moving-to-stop (chuyển trạng thái quan sát được), road
incident (sự cố có bằng chứng ngữ cảnh), accident (va chạm quan sát được).
Không gán accident chỉ vì đứng yên. ROI được duyệt riêng, cũng cần recording
metadata và điều kiện quan sát. Không suy location từ overlay chưa xác nhận.

Nhãn negative phải có khoảng thời gian đã xem đầy đủ; file không có event
không tự là negative. Không tính vùng unknown/chưa xem như TN. Track IDs là
scope pipeline, không phải danh tính xe; annotation theo đối tượng/interval
trong video và liên kết IDs sau khi annotation hoàn tất.

## 2. Split và hiệu chỉnh

Tách development/test theo video gốc hoặc recording session; các clip liền
kề/overlap không chia sang hai tập. Nếu có đủ camera, thêm camera holdout để
đánh giá khả năng chuyển sang góc nhìn khác. Ngưỡng chỉ chọn trên development;
khóa config, model weights, ROI và code/version trước khi chạy test.

Sáu clip demo hiện có chỉ dùng smoke test, không đủ để chọn ngưỡng hoặc chứng
minh tổng quát. Không dùng fixtures để báo cáo accuracy trên dữ liệu thật.

## 3. Event matching và metrics

Protocol dự kiến: chỉ ghép predicted/ground-truth interval cùng camera/video
và cùng loại mục tiêu. Temporal IoU = thời lượng giao / thời lượng hợp.
Ngưỡng ban đầu đề xuất 0.3, phải khóa trước test và báo thêm sensitivity
0.1/0.5. Matching một-một tối đa tổng IoU trong các cặp đủ ngưỡng; mỗi ground
truth chỉ nhận một TP. Các ứng viên trùng còn lại tính FP. Không lấy union
mọi ứng viên rồi bỏ qua duplicates. Đây là quy ước đánh giá của project,
chưa phải benchmark được chạy hoặc tiêu chuẩn đã được nghiệm thu.

Precision = TP/(TP+FP); recall = TP/(TP+FN); F1 từ precision/recall. Nếu mẫu
số bằng 0, ghi undefined cùng counts, không tự gán 100%. Báo cáo riêng từng
camera, tình huống, ROI/threshold version và số event đã được annotation.

False alarms/camera-hour: số FP chia tổng thời lượng các interval camera đã
xem và có thể quan sát, tránh đếm trùng intervals overlap. Báo cáo cả thời
lượng làm mẫu số. Dữ liệu 60 giây demo quá ngắn để diễn giải tỷ lệ ổn định.
Detection delay = timestamp đầu tiên rule phát ứng viên trừ onset ground
truth, không dùng start_sec hồi cứu làm thời điểm phát. Cần bổ sung trường
thời điểm phát/report khi triển khai evaluator; v2 hiện giữ evidence cuối
để kiểm tra trigger của baseline.

## 4. Tiêu chí báo cáo coursework

- Tách kết quả kiểm thử kỹ thuật (resume, schema, PTS, runtime) khỏi hiệu năng
  CV (TP/FP/FN, delay, track quality).
- Báo confusion giữa “đứng yên/chờ đèn” và sự cố; giữ ví dụ FP/FN để giải thích.
- Báo thời gian CPU/GPU, peak VRAM, các camera/clip được xử lý và vùng unknown.
- Không tuyên bố nhận diện tai nạn hoặc gợi ý điều phối đã đạt chất lượng khi
  chỉ có smoke test/synthetic fixtures.

Evaluator executable, annotation validator và split manifest đã có trong
T6.1; template demo giữ pending/unassigned. Đánh giá chất lượng thật cần nhãn
độc lập; không tạo nhãn giả cho footage demo.
