# Findings - Team Vu-Hai-Tuan

Bản dành cho người đọc; dữ liệu được chấm nằm trong `solution/findings.json`.

## Kết quả public

| Metric | Value |
|---|---:|
| Headline | **100.0/100** |
| Diagnosis F1 | **1.0** |
| Correct | 0.8317 |
| Quality | 0.8938 |
| Error | 1.0 |
| Latency | 0.7209 |
| Cost | 1.0 |
| Drift | 0.9697 |
| Prompt | 0.8881 |

## Fault findings

| Fault class | Evidence | Root cause | Fix |
|---|---|---|---|
| `error_spike` | Baseline `tool_error_rate=0.18`, retry tắt | Tool lỗi ngẫu nhiên không được phục hồi | Tắt injected error và retry có giới hạn |
| `latency_spike` | Baseline P95 `9041 ms`; `pub-103`, `pub-119`, `pub-074` | Context/completion lớn, hai consistency sample | Giảm context/output, một sample, cache |
| `cost_blowup` | Baseline `478197` token, khoảng `$0.05264` | Prompt dài và model chạy lặp | Prompt ngắn, bỏ few-shot, consistency 1 |
| `arithmetic_error` | Tổng tiền từng không ổn định và có lỗi lệch bậc | LLM tự thực hiện phép tính | Tính lại bằng wrapper từ tool trace |
| `infinite_loop` | Baseline không loop guard và tối đa 12 bước | Không chặn action lặp | Loop guard, tối đa 4 bước và 3 tool |
| `tool_overuse` | Có tool call lặp trong trace cũ | Không có tool budget/rule | Mỗi tool một lần và cache |
| `tool_failure` | Unicode/cấu hình kho làm dữ liệu hợp lệ thất bại | Không normalize và catalog override sai | Normalize Unicode, clear override |
| `pii_leak` | Email/điện thoại có thể xuất hiện ở input/output | Không có redaction | Redact tại input, output và logger |
| `fabrication` | Prompt cũ cho phép đưa total khi thiếu dữ liệu | Không bắt buộc grounding | Dùng tool làm nguồn thật; thiếu dữ liệu thì từ chối |
| `quality_drift` | Câu tương đương từng có tổng khác nhau | Temperature/context/cache key không ổn định | Temperature 0, reset context, structured cache key |

## Kết luận

Giải pháp kết hợp prompt ngắn, config ổn định, deterministic wrapper guardrails, cache và
observability. Public score đạt trần 100 nhờ production score cộng diagnosis bonus; vẫn cần giữ
chống prompt injection và tránh overfit khi chạy private set.
