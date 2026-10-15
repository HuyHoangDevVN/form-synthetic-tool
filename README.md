# 200 phản hồi tổng hợp cho form kiểm thử

Python 3.8+, chỉ dùng thư viện chuẩn. Tool này gắn với một form cụ thể
(URL và entry ID trong `tool.py`), không phải trình gửi cho mọi Google Form.

## Chạy từ checkout mới

```powershell
python -m unittest -v
python tool.py diagnose
python tool.py revise
python tool.py send --revised --limit 1
# Kiểm tra phản hồi đủ 31 câu trên Forms/Sheet, rồi gửi phần còn lại:
python tool.py send --revised
```

Chỉ chạy các lệnh `send` khi muốn thêm dữ liệu vào form kiểm thử.
Repo chỉ lưu code, test và hướng dẫn. CSV, nhật ký gửi, schema tải về và
báo cáo được bỏ qua bởi `.gitignore`; giữ chúng ở máy khi đang gửi dở.
Không thêm dependency hoặc yêu cầu cài đặt ngoài Python.

## Bổ sung 36 dòng

`python tool.py extend` tạo `responses-topup-36.csv` dựa trên tỷ lệ từng câu
trong CSV v2 bằng phương pháp phần dư lớn nhất, rồi xáo trộn với seed cố định.
`distribution-total-236.json` ghi phân bố kết hợp 236 dòng. Số nguyên có thể
làm tỷ lệ lệch nhẹ. Lệnh không ghi đè CSV có sẵn và chưa gửi phản hồi.
`python tool.py send --topup` gửi tối đa 36 dòng bổ sung bằng nhật ký riêng
`send-log-topup-36.jsonl`; không gửi lại 200 dòng v2.

## Bộ dữ liệu điều chỉnh

`python tool.py revise` tạo riêng `responses-revised-v2.csv` và báo cáo đếm
`distribution-revised-v2.json`, không ghi đè dữ liệu/nhật ký cũ và không gửi.
Giới tính: Nam 92, Nữ 92, Khác 16 (8%). Khối ngành: Sức khỏe 40 (20%),
Kỹ thuật – Công nghệ 50 (25%), Kinh tế - Kinh doanh 44 (22%), Khoa học
xã hội – Nhân văn 36 (18%), Nghệ thuật và Thiết kế 30 (15%).
Mỗi câu Likert có phân bố riêng: mức 1 là 4–10 dòng, mức 2 là 8–14,
mức 3 là 40–60, mức 4 là 70–90, mức 5 nhận phần còn lại. Tổng mức 1–2
là 6–12%, mức 3–5 là 88–94%. Không đảo chiều các câu diễn đạt tiêu cực:
mức cao chỉ là đồng ý, không mặc nhiên là kết quả tốt.
Lệnh `send` mặc định vẫn dùng CSV cũ. `python tool.py send --revised` gửi CSV
v2 với nhật ký riêng `send-log-revised-v2.jsonl`. Chỉ gửi lô thay thế sau khi
đã xử lý phản hồi cũ trên Google; tool không tự xóa dữ liệu trên form.

Python 3.8 trở lên, không cần cài thư viện. Chạy PowerShell tại folder này:

```powershell
python tool.py prepare
python -m unittest -v
python tool.py diagnose
python tool.py send
```

`prepare` chỉ đọc form và xuất `responses.csv` (200 dòng), `questions.json`
(ánh xạ entry ID sang câu hỏi). File CSV mở được bằng Excel. Đã chạy prepare
thì không cần chạy lại; nếu chạy lại, lệnh kiểm tra CSV và giữ nguyên file có sẵn.

`send` gửi đúng CSV đã tạo lên form được cấu hình trong `tool.py`, tuần tự,
nghỉ 0,5 giây sau mỗi phản hồi (chưa tính thời gian mạng). Tổng thời gian nghỉ
cho 200 dòng là 99,5 giây, thay vì 597 giây trước đây. Vẫn gửi tuần tự và dừng
ngay khi lỗi; không gửi song song hoặc tự thử lại. Chỉ chạy khi muốn thêm dữ
liệu vào form. Không thêm nhãn
vào câu trả lời; tính tổng hợp được ghi trong tài liệu này và nhật ký riêng.

5 khối ngành, mỗi ngành 40 phản hồi. Mỗi câu còn lại cũng chia đều các lựa
chọn rồi xáo trộn độc lập với seed cố định. Đây là dữ liệu để kiểm tra biểu
đồ và luồng nhập liệu, không mô phỏng tương quan hay thực trạng sinh viên.

`send-log.jsonl` ghi lần thử và kết quả. HTTP 200 một mình không được coi là
thành công: cần có phần tử xác nhận trong trang trả về. Tool dừng khi lỗi,
CAPTCHA, đăng nhập hoặc không thấy xác nhận; không tự retry. Chạy `send` lại
sẽ bỏ qua những dòng đã nhận xác nhận. Nếu bị ngắt giữa lần gửi, tool báo
dòng chưa rõ kết quả và dừng. Đối chiếu phản hồi trong Google Forms/Sheet:

```powershell
# Chỉ khi đã xác minh dòng chưa rõ ĐÃ được Google lưu:
python tool.py send --resolve-last saved
# Hoặc chỉ khi đã xác minh dòng đó CHƯA được lưu:
python tool.py send --resolve-last not-saved
```

Không đoán kết quả dựa riêng vào lỗi mạng: Google có thể đã lưu trước khi
kết nối bị ngắt. Không xóa nhật ký, sửa/thay CSV khi đang gửi dở, hoặc chạy
nhiều tiến trình gửi cùng lúc. Tool không tự đối chiếu dữ liệu phía Google.

Cấu trúc nhúng và class xác nhận của Google không phải API ổn định; nếu
Google thay đổi, cần cập nhật parser. Không có proxy, giả fingerprint hay
vượt CAPTCHA. Không trộn bộ dữ liệu này vào kết quả nghiên cứu thực tế.

## Sửa lỗi form nhiều trang

Bản cũ thiếu metadata điều hướng trang nên trang xác nhận không chứng minh
các câu trả lời đã được lưu. Bản sửa đọc các trường ẩn mới cho từng lượt gửi
và gửi `pageHistory` bao gồm toàn bộ trang. `diagnose` chỉ đọc, không gửi.
Test cục bộ kiểm tra payload, chưa chứng minh dữ liệu được lưu phía Google.
Cần đối chiếu một phản hồi đủ 31 câu trên Forms/Sheet trước khi gửi hàng loạt.

Gửi thử tối đa một dòng đang chờ: `python tool.py send --limit 1`.
Sau khi kiểm tra dữ liệu phía Forms, `python tool.py send` tiếp tục các dòng
còn lại theo nhật ký; không gửi lại dòng đã nhận xác nhận. `--limit` nhận 1–200.

Nhật ký cũ không có `protocol: 2` bị chặn, kể cả dòng từng nhận xác nhận.
Không dùng `--resolve-last` để coi cả lô cũ là thành công. Giữ CSV và nhật ký
cũ; đối chiếu từng phản hồi trên Forms/Sheet trước khi quyết định làm lô mới.
Tool không xóa hoặc sửa các phản hồi rỗng đã tồn tại phía Google.
