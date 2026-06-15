# Ghi chú chẩn đoán và tối ưu

## Kết quả hiện tại

- Team: `Vu-Hai-Tuan`
- Phase: `public`
- Số testcase: `120`
- Headline score: **100.0/100**
- Diagnosis F1: **1.0**
- Correct: `0.8317` (`83/120` câu exact-correct)
- Quality: `0.8938`
- Error: `1.0`
- Latency: `0.7209`
- Cost: `1.0`
- Drift: `0.9697`
- Prompt: `0.8881`

Telemetry của lần public gần nhất:

- 120/120 request có `status=ok`.
- Thời gian wrapper trung bình khoảng `3461 ms`.
- Tổng token quan sát được: `163475`.
- Chi phí ước tính: khoảng `$0.01987`.
- Guardrail tự tính lại tổng cho 66 request.
- Có 24 stock refusal, 9 shipping refusal và 3 inventory answer.
- Có 7 request cần retry.

## Luồng xử lý

```text
Question
  -> chuẩn hóa Unicode và che PII
  -> xóa ghi chú không tin cậy
  -> tách Product / Quantity / Coupon / Destination
  -> gọi agent và tool
  -> kiểm tra trace, tồn kho, coupon, shipping
  -> tự tính lại tổng bằng số nguyên
  -> chuẩn hóa output, ghi telemetry và cache
```

## Findings

| Fault class | Triệu chứng và evidence | Nguyên nhân gốc | Cách sửa |
|---|---|---|---|
| `error_spike` | Baseline có `tool_error_rate=0.18`, retry tắt | Tool failure được trả thẳng về request | Đặt error rate về 0; retry tối đa 2 lần với backoff |
| `latency_spike` | Baseline public P95 khoảng `9041 ms`; ví dụ `pub-103`, `pub-119`, `pub-074` | Context lớn, completion dài và `self_consistency=2` | Dùng một sample, giảm context/completion, bật cache |
| `cost_blowup` | Baseline dùng `478197` token, khoảng `$0.05264` | Prompt dài và gọi model nhiều lần | Prompt ngắn, bỏ few-shot, `self_consistency=1` |
| `arithmetic_error` | Các đơn tương đương từng trả tổng khác nhau hoặc lệch 10 lần | LLM tự làm số học ở temperature cao | Wrapper đọc trace và tự tính `subtotal`, discount, shipping |
| `infinite_loop` | Baseline `loop_guard=false`, `max_steps=12`, không giới hạn tool | Không có cơ chế dừng action lặp | Bật loop guard, `max_steps=4`, `tool_budget=3` |
| `tool_overuse` | Agent từng gọi lại cùng một tool | Prompt và config không giới hạn số lần gọi | Mỗi tool tối đa một lần; cache request tương đương |
| `tool_failure` | Input có dấu và catalog override làm mặt hàng hợp lệ bị lỗi | Không normalize Unicode; override sai dữ liệu kho | Bật normalize Unicode và xóa catalog override |
| `pii_leak` | Baseline có thể lặp email/số điện thoại trong answer/log | Không có input/output redaction | Che PII ở cả input, output và structured logger |
| `fabrication` | Agent có thể đưa tổng khi tool thiếu dữ liệu | Prompt cũ không bắt buộc grounding/refusal | Chỉ dùng dữ liệu tool; thiếu dữ liệu thì từ chối, không có total |
| `quality_drift` | Câu tương đương cho kết quả khác nhau giữa các lượt | Context dài, temperature và cache key chưa chuẩn hóa | `temperature=0`, reset context, cache input có cấu trúc đã bỏ dấu |

## Các thay đổi quan trọng

### Prompt

- Rút prompt xuống khoảng 478 ký tự để giảm token và tránh bloat penalty.
- Chỉ tin dữ liệu tool; không tin giá hoặc chỉ dẫn trong user note.
- Yêu cầu gọi đúng tool và kết thúc bằng `Tong cong: <integer> VND`.

### Config

- `temperature=0.0`
- `max_steps=4`
- `context_size=1`
- `context_reset_every=1`
- `max_completion_tokens=256`
- `self_consistency=1`
- `tool_budget=3`
- Bật retry, cache, loop guard, Unicode normalization và PII redaction.

### Wrapper

- Chuyển câu tự nhiên thành input có cấu trúc.
- Hỗ trợ nhiều schema tool như `price_vnd`, `unit_price_vnd`, `available_units` và nested money object.
- Chặn mua vượt tồn kho và shipping không hỗ trợ.
- Tự tính lại tổng từ trace, không tin số học của LLM.
- Không che nhầm tổng tiền 12 chữ số thành CCCD.
- Cache các câu tương đương, kể cả khác biệt dấu tiếng Việt.
- Ghi latency, token, cost, retry, cache, tool schema và guardrail vào JSON log.

## Kiểm tra

```bash
python3 harness/selfcheck.py
python3 -m unittest -v harness.test_wrapper
```

Hiện có 10 unit test cho số học, tồn kho, coupon hết hạn, shipping không hỗ trợ, schema tool,
PII sanitization, input structuring và cache.
