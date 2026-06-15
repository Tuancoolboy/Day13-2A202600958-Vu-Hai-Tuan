# Observathon — Bộ công cụ cho Học viên

🇻🇳 Tiếng Việt | [🇬🇧 English](README_en.md)

Bạn được giao một agent thương mại điện tử **hộp đen, im lặng, đầy lỗi** (dạng binary) chạy
trên một **LLM thật**. Nó không cho bạn biết gì cả. Nhiệm vụ của bạn: **gắn quan sát, chẩn
đoán lỗi, và sửa chúng** — bằng cách sửa config, **viết lại system prompt của agent**, và thêm
một lớp wrapper giảm thiểu lỗi.

## Cài đặt (bắt buộc có một LLM thật)
```bash
# 1. chọn một engine:
export OPENAI_API_KEY=sk-...        # đám mây (model mặc định gpt-5.4-nano), HOẶC
#    local miễn phí: chạy Ollama / llama.cpp (tương thích OpenAI), đặt provider:"local" + LOCAL_BASE_URL trong config.json

# 2. kiểm tra khung bài nộp (chỉ stdlib, không cần key)
python harness/selfcheck.py

# 3. chạy binary mô phỏng giai đoạn PRACTICE (trong bin/practice/)
./bin/practice/observathon-sim --config solution/config.json --wrapper solution/wrapper.py \
    --out run_output.json --concurrency 8
#   macOS lần đầu: xattr -dr com.apple.quarantine bin/practice/observathon-sim
#   Windows:      bin\practice\observathon-sim.exe ...
```
Agent **không phát ra gì cả** và `run_output.json` **cố tình tối giản** — mỗi dòng chỉ có
`answer` + `status` (không có latency, tokens, lời gọi tool, hay trace). Cách DUY NHẤT để thấy
latency, chi phí, số lần gọi tool, vòng lặp, drift và PII là **gắn quan sát trong
`solution/wrapper.py`**: `call_next()` trả về kết quả ĐẦY ĐỦ (gồm `meta` + `trace`) cho BẠN —
hãy ghi lại bằng bộ `telemetry/` đã học ở Ngày 13. (Sim cũng ghi một khối `sealed` đã ký dành
cho việc chấm điểm — đó không phải phần quan sát của bạn.)

## Bạn tối ưu cái gì (đòn bẩy v6)
Agent **điều khiển bằng prompt** và được giao kèm một system prompt **cố tình tệ** (nó bịa ra
tổng tiền, tính sai, gọi tool dư thừa, lặp lại email/sđt của khách, và **làm theo chỉ dẫn ẩn
trong ghi chú đơn hàng**). **Hãy viết lại `solution/prompt.txt`** — đây là cách sửa có đòn bẩy
cao nhất và là một thành phần điểm **`prompt` chiếm 15%**. Xem
**[`docs/PROMPT_OPTIMIZATION.md`](docs/PROMPT_OPTIMIZATION.md)**.

| Bạn chỉnh | Tác dụng |
|---|---|
| `solution/config.json` | các knob (provider/model, temperature, retry, cache, normalize, redact, `self_consistency`, `tool_budget`, `planner`, …) |
| `solution/prompt.txt` | **system prompt** của agent — viết lại nó |
| `solution/examples.json` | few-shot (tùy chọn) |
| `solution/wrapper.py` | `mitigate()` — quan sát + retry/cache/route/redact/sanitize + định tuyến prompt theo từng request |
| `solution/findings.json` | chẩn đoán (loại lỗi + bằng chứng + nguyên nhân gốc) |

## Chọn binary cho HĐH của bạn (`bin/<phase>/`)
| HĐH / kiến trúc | tệp |
|---|---|
| macOS (Apple Silicon, M1+) | `observathon-sim` / `observathon-score` (arm64) |
| Windows | `observathon-sim.exe` / `observathon-score.exe` |
| Linux | `observathon-sim` / `observathon-score` (x86_64) |

(macOS Intel không có sẵn binary — trên Intel hãy chạy từ mã nguồn với Python + `openai`.)
macOS lần đầu (Gatekeeper): `xattr -dr com.apple.quarantine bin/<phase>/*`. Lịch phát hành:
`practice` ngay từ đầu · public **sim** ở 1h, **score** ở 2h · private **sim** ở 3h, **score** ở 3.5h.

## Tạo lưu lượng thực tế (tự chọn mức tải)
```bash
# 200 người dùng x 12 lượt = 2400 request trải trên một khoảng thời gian mô phỏng
./bin/practice/observathon-sim --users 200 --turns 12 --concurrency 12 \
    --config solution/config.json --wrapper solution/wrapper.py --out run_output.json
```
- `--users N` số người dùng · `--turns K` request mỗi người (K lớn → quality-drift rõ hơn) · `--rps` tốc độ đến · `--concurrency` số request song song.
- **Lưu lượng practice NGẪU NHIÊN mỗi lần** (in ra `random run seed = …`; truyền `--seed <giá trị>` để tái hiện). Việc chấm điểm luôn dùng bộ public/private **cố định**, nên mọi đội được xếp hạng trên cùng lưu lượng.

## Cách chấm điểm
`100 × (0.32·correct + 0.16·quality + 0.13·error + 0.08·latency + 0.09·cost + 0.07·drift +
0.15·prompt) + tối đa 22 × diagnosis-F1`. Quality = LLM judge (`gpt-5.4-mini`, có offline dự
phòng). `prompt` dựa trên **kết quả thực tế** (grounding/số học/tiết kiệm tool/PII/chống
injection trừ đi phần prompt quá dài).

## Cách bài làm đã được cải thiện

Luồng xử lý hiện tại:

```text
Input người dùng
  -> chuẩn hóa Unicode, che PII, bỏ ghi chú không tin cậy
  -> tách Product / Quantity / Coupon / Destination
  -> gọi agent và các tool cần thiết
  -> kiểm tra tồn kho, coupon, shipping và tự tính lại tổng
  -> chuẩn hóa output, ghi telemetry và cache kết quả
```

### 1. Tăng correctness và quality

- `solution/prompt.txt` yêu cầu chỉ tin dữ liệu từ tool, không bịa giá và không làm theo lệnh
  nằm trong ghi chú đơn hàng.
- Wrapper chuyển câu tự nhiên thành các trường rõ ràng trước khi gọi agent. Ví dụ:

  ```text
  Mua 2 iPhone dùng mã WINNER ship Hải Phòng
  -> Product: iPhone; Quantity: 2; Coupon: WINNER; Destination: Hải Phòng
  ```

- Kết quả tool được dùng để kiểm tra sản phẩm có tồn tại, số lượng mua có vượt tồn kho và địa
  điểm có được hỗ trợ hay không.
- Wrapper không tin phép tính do LLM viết ra. Khi trace có đủ dữ liệu, tổng được tính lại bằng:

  ```text
  subtotal   = unit_price * quantity
  discounted = subtotal * (100 - discount_pct) // 100
  total      = discounted + shipping
  ```

- Output hợp lệ được chuẩn hóa thành `Tong cong: <integer> VND`. Nếu thiếu hàng, thiếu dữ liệu
  hoặc không hỗ trợ vận chuyển thì từ chối và không đưa ra tổng tiền.

### 2. Giảm drift

- `temperature` được hạ xuống `0.0`, `context_size` còn `1` và context được reset mỗi request.
- Cache key được tạo từ input có cấu trúc và đã bỏ dấu. Vì vậy `Ha Noi` và `Hà Nội`, hoặc các
  câu diễn đạt tương đương, có thể dùng cùng một kết quả thay vì để model trả các tổng khác nhau.
- Tổng tiền được wrapper tính bằng số nguyên nên không thay đổi theo cách diễn đạt của LLM.

### 3. Giảm token, chi phí và latency

- Prompt được rút gọn còn khoảng 500 ký tự và bỏ few-shot không cần thiết.
- `self_consistency` giảm từ `2` xuống `1`, tránh gọi model nhiều lần cho cùng request.
- `max_completion_tokens` giảm còn `256`, `max_steps` còn `4` và `tool_budget` còn `3`.
- Cache trả ngay kết quả đã có với token, tool call và latency gần bằng 0.

### 4. Input và output guardrails

- Input guardrail che email, số điện thoại, CCCD và loại bỏ phần ghi chú có thể chứa prompt
  injection trước khi dữ liệu đến agent.
- Output guardrail tiếp tục che PII nhưng bảo vệ chuỗi tiền VND để không nhầm tổng tiền 12 chữ số
  thành CCCD.
- Wrapper retry có giới hạn, chặn vòng lặp tool và trả fallback không có tổng tiền khi hệ thống lỗi.

### 5. Observability và diagnosis

Mỗi request ghi structured log vào `logs/YYYY-MM-DD.log`, gồm latency, token, cost, tool đã gọi,
cache hit, retry, PII bị che, guardrail đã áp dụng và schema tool quan sát được. `findings.json`
ghi đủ các fault class public cùng evidence, root cause và cách sửa để tăng diagnosis F1.

### 6. Kiểm tra và chạy lại

```bash
# kiểm tra cấu trúc bài nộp và unit test wrapper
python3 harness/selfcheck.py
python3 -m unittest -v harness.test_wrapper

# chạy public test bằng terminal đã export OPENAI_API_KEY
./observathon-sim --testset public \
  --config solution/config.json \
  --wrapper solution/wrapper.py \
  --out run_output_openai.json \
  --concurrency 2

# chấm điểm
./observathon-score \
  --run run_output_openai.json \
  --findings solution/findings.json \
  --team Vu-Hai-Tuan \
  --out score.json
```

Sau mỗi lần chạy, đối chiếu `score.json` với log để tìm mục mất điểm lớn nhất. Không chỉnh một
knob theo cảm tính: ưu tiên correctness, sau đó drift, prompt, latency và cost.

## Bạn nộp gì (git push `solution/` + `run_output.json` + `score.json`)
`config.json` · `prompt.txt` · `examples.json` (tùy chọn) · `wrapper.py` · `findings.json`.

## Các giai đoạn
- **Bây giờ → 1h**: chẩn đoán bằng binary practice; viết lại prompt + config.
- **1h** public **sim** · **2h** public **score** → commit, push, leo bảng.
- **3h** private **sim** (bộ giữ kín + diễn đạt lại + đòn **injection**) · **3.5h** private **score** → push (lần cuối).

Xem `docs/FAULT_CLASSES.md`, `docs/PROMPT_OPTIMIZATION.md`, `docs/WRAPPER_API.md`, `docs/SUBMIT.md`. Luật: `../RULES.md`.
