# Synthetic Form Tool

Sinh và gửi phản hồi tổng hợp cho Google Forms do bạn quản lý để kiểm thử
form, biểu đồ và luồng nhập liệu. Python 3.8+, chỉ dùng thư viện chuẩn.
Dữ liệu không đại diện cho thực trạng hay kết quả nghiên cứu.

## Cấu hình và sử dụng

```powershell
Copy-Item config.example.json config.local.json
# Sửa form_url, batch_id, count và các tỷ lệ trong config.local.json.
python tool.py diagnose --config config.local.json
python tool.py prepare --config config.local.json
```

`diagnose` chỉ đọc, in entry ID, tên câu hỏi, lựa chọn và lịch sử trang.
`prepare` tạo CSV, manifest và báo cáo phân bố, chưa gửi. Kết quả ở:

```text
.batches/<form-id>/<batch_id>/responses.csv
.batches/<form-id>/<batch_id>/manifest.json
.batches/<form-id>/<batch_id>/distribution.json
```

Xem CSV/báo cáo trước khi gửi thử:

```powershell
python tool.py send --config config.local.json --limit 1
# Đối chiếu dữ liệu đủ các câu trong Forms/Sheet, rồi gửi phần còn lại:
python tool.py send --config config.local.json
```

`send` thay đổi dữ liệu trên form. Tool gửi tuần tự, nghỉ 0,5 giây giữa
các lượt và dừng ngay khi lỗi; không tự retry hay gửi song song.
Không cần đổi `count` để gửi tiếp: giữ cùng config và chạy `send` lại.
`--limit` chỉ giới hạn số dòng đang chờ gửi trong một lần chạy.

## Các trường cấu hình

| Trường | Ý nghĩa |
|---|---|
| `form_url` | Link đầy đủ `https://docs.google.com/forms/d/e/ID/viewform` hoặc `formResponse`. Không dùng link rút gọn `forms.gle`. |
| `batch_id` | Tên lô duy nhất trong cùng form; 1–64 ký tự chữ/số, `_`, `-`. Giữ nguyên khi chạy tiếp. |
| `count` | Tổng số dòng của lô, số nguyên dương; không cố định 200 hoặc 36. |
| `seed` | Seed xáo trộn có thể tái lập; mặc định `20261015`. |
| `distributions` | Ánh xạ `entry.ID` sang trọng số của **tất cả** lựa chọn trong câu đó. Mặc định `{}`. |
| `likert_weights` | Tùy chọn: 5 trọng số dùng cho các câu có nhãn bắt đầu bằng `1` đến `5`, ví dụ `[4,6,25,40,25]`. |

Trọng số không cần tổng bằng 100: `[46,46,8]` và `[0.46,0.46,0.08]`
có cùng ý nghĩa. Giá trị phải hữu hạn, không âm, tổng lớn hơn 0. Trọng số
0 loại lựa chọn đó khỏi dữ liệu được sinh. Câu không có cấu hình riêng
hoặc không khớp thang Likert được chia đều. Phân bố riêng theo entry ID
ưu tiên hơn `likert_weights`; có thể đặt tỷ lệ khác nhau cho từng câu.

Các nhãn trong `distributions` phải trùng chính xác nhãn trên form, kể cả
dấu cách và dấu gạch. `diagnose` cho biết ID/nhãn cần dùng. Ví dụ trong
`config.example.json` ứng với form trước đây; khi đổi form, cập nhật hoặc
xóa các entry không còn tồn tại. Tool từ chối ID/lựa chọn sai thay vì bỏ qua.

Số lượng mỗi lựa chọn được chia theo phương pháp phần dư lớn nhất để
luôn đủ `count`. Sai lệch so với hạn ngạch lý tưởng nhỏ hơn một dòng
mỗi lựa chọn; trường hợp hòa được ưu tiên theo thứ tự lựa chọn trên form.
Thứ tự trả lời được xáo trộn độc lập theo seed, không mô phỏng tương quan
giữa các câu. Mức 4–5 nghĩa là đồng ý, không tự đảo chiều câu tiêu cực.

## Chạy tiếp và tránh gửi trùng

Mỗi lô có `send-log.jsonl` riêng, gắn với SHA-256 của form/cấu hình, schema
và toàn bộ CSV. Dòng đã nhận xác nhận hoặc đã được đối chiếu là lưu thành
công sẽ được bỏ qua. Sửa CSV, config hoặc schema khi đang gửi dở làm tool
dừng; khôi phục đúng bản gốc trước khi chạy tiếp. `prepare` lại không ghi đè.

Khóa `send.lock` ngăn hai tiến trình gửi cùng một lô. Ctrl+C thông thường
sẽ nhả khóa; nếu tiến trình bị kill/máy mất điện, khóa có thể còn. Chỉ xóa
file khóa sau khi chắc chắn không còn tiến trình gửi và đã kiểm tra nhật ký.
Không xóa nhật ký, manifest hay đổi `batch_id` để né một lần gửi chưa rõ.

Nếu timeout/Ctrl+C xảy ra trong lúc gửi, Google có thể đã lưu dữ liệu.
Tool dừng ở dòng chưa rõ kết quả, yêu cầu đối chiếu Forms/Sheet:

```powershell
# Chỉ khi đã kiểm tra dòng chưa rõ ĐÃ được Google lưu:
python tool.py send --config config.local.json --resolve-last saved
# Hoặc chỉ khi đã kiểm tra dòng đó CHƯA được lưu:
python tool.py send --config config.local.json --resolve-last not-saved
```

Hai lệnh trên ghi kết quả đối chiếu rồi tiếp tục lô; không tự kiểm chứng
phía Google. Không thể bảo đảm exactly-once qua lỗi mạng vì Google Forms
không cung cấp khóa idempotency cho cách gửi này. Không đổi sang máy khác
mà bỏ quên thư mục lô. Lô khác hoặc bản clone khác không chia sẻ khóa/nhật ký.
Batch ID mới được coi là **một lô mới**, có thể thêm phản hồi trùng với lô cũ.

Muốn bổ sung 36 dòng: dùng config mới với `count: 36`, `batch_id` mới và
giữ tỷ lệ đã chọn. Phân bố sau khi gộp có thể lệch nhẹ do làm tròn. Để bám
đúng phân bố thực tế lô cũ, đặt trọng số từng câu bằng số lượng lựa chọn
trong báo cáo `distribution.json` của lô đó.

## Tương thích dữ liệu cũ

CLI mới yêu cầu `--config`; thay cho `revise`, `extend`, `--revised`,
`--topup` và các đường dẫn CSV cố định. Những CSV và log cũ ở thư mục gốc
được giữ nguyên, không tự nhập vào lô mới hoặc gửi lại. Các lô cũ đã gửi
200 + 36 dòng không tự được tạo thành lô `example-200` trong cấu hình mẫu.
Không chạy lệnh gửi của ví dụ trên form cũ nếu không muốn thêm một lô mới.

Nhật ký phiên bản 2 trở xuống không có hash đầy đủ nên không được dùng
để tiếp tục lô phiên bản 3. Giữ bản cũ làm bằng chứng và đối chiếu dữ liệu
phía Google trước khi quyết định tạo một lô mới. Không có bước xóa phản hồi.

## Kiểm tra và giới hạn

```powershell
python -m unittest -v
```

Test dùng mạng giả lập, kiểm tra phân bố, cấu hình, khóa, hash, resume và
xử lý gián đoạn. Test PASS không chứng minh dữ liệu đã lưu phía Google.
Trang xác nhận chỉ chứng minh máy chủ trả trang xác nhận; cần đối chiếu
ít nhất một phản hồi đủ câu trả lời trên Forms/Sheet trước khi gửi hàng loạt.

Hỗ trợ câu chọn một và dropdown, cùng các trang tuần tự. Tool từ chối
checkbox, văn bản, lưới, upload, thang loại khác, lựa chọn Other nhập tự do
và rẽ nhánh. Form yêu cầu đăng nhập/CAPTCHA không được vượt qua. Cấu trúc
HTML nhúng và class xác nhận không phải API ổn định của Google; có thể
cần cập nhật parser khi Google thay đổi.

`.batches/`, `config.local.json`, CSV, log và báo cáo cũ được bỏ qua bởi
Git. Repo chỉ lưu code, test, hướng dẫn và cấu hình mẫu.
