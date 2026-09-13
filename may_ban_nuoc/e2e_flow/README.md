# Chạy kiểm thử giọng nói bằng 5 terminal

Chạy các lệnh từ thư mục `may_ban_nuoc/` có chứa `e2e_flow/`, `models/` và `actions/`. Dùng môi trường `../venv`, model có sẵn và loa/micro đã cấu hình trong `e2e_flow/config.json`.

Khởi động terminal 1 trước, tiếp theo 2–4, cuối cùng 5. Cả 5 terminal phải dùng cùng thư mục `--output` **mới, chưa chứa dữ liệu**. Mỗi lần chạy đổi `demo_001` thành tên khác.

## 5 terminal

### Terminal 1 — Ghi log

```bash
../venv/bin/python -m e2e_flow.log_server --output e2e_flow/runs/demo_001
```

Nhận sự kiện, lấy mẫu CPU/RAM/nhiệt độ và tính delay từ timestamp.

### Terminal 2 — Action server

```bash
../venv/bin/python -m e2e_flow.action_server --output e2e_flow/runs/demo_001
```

Xử lý giỏ hàng, đơn hàng và thanh toán qua logic nghiệp vụ và SQLite hiện có.

### Terminal 3 — Rasa

```bash
../venv/bin/python -m e2e_flow.rasa_server --output e2e_flow/runs/demo_001 --model models/20260822-170846-calm-allegory.tar.gz
```

Nhận văn bản, phân tích intent/entities, điều phối hội thoại và gọi action.

### Terminal 4 — Âm thanh

```bash
../venv/bin/python -m e2e_flow.voice_server --output e2e_flow/runs/demo_001
```

Phát câu kịch bản qua loa, thu micro, chuyển giọng nói thành văn bản bằng Whisper STT và đọc phản hồi bằng Piper TTS.

### Terminal 5 — Chạy kịch bản

```bash
../venv/bin/python -m e2e_flow.run --output e2e_flow/runs/demo_001
```

Chờ các dịch vụ sẵn sàng, chạy lần lượt hai session trong `sessions.json` và kiểm tra kết quả từng lượt. Sau khi terminal 5 kết thúc, nhấn Ctrl+C tại terminal 1–4 để dừng dịch vụ.

## Các file chính

| File/thư mục | Vai trò |
|---|---|
| `log_server.py` | Dịch vụ ghi log và lấy mẫu tài nguyên — terminal 1 |
| `action_server.py` | Dịch vụ action có ghi timestamp — terminal 2 |
| `rasa_server.py` | Dịch vụ Rasa có đo thời gian NLU/hội thoại — terminal 3 |
| `voice_server.py` | Thu/phát âm thanh, VAD, STT và TTS — terminal 4 |
| `run.py` | Điều phối và chạy kịch bản — terminal 5 |
| `config.json` | Cổng dịch vụ, model, thiết bị âm thanh và thời gian chờ |
| `sessions.json` | Câu thoại và kết quả kỳ vọng của hai session |
| `evaluation.py` | Kiểm tra intent, action, giỏ hàng và độ chính xác STT |
| `vad_gate.py` | Phát hiện tiếng nói theo mức ồn nền |
| `common.py` | Giao tiếp HTTP và gửi sự kiện |
| `support/runtime.py` | Tham số khởi động và ghi timestamp trong dịch vụ |
| `support/report.py` | Ghép timestamp, tính delay và định dạng log |
| `diagnostics/` | Công cụ kiểm tra thiết bị âm thanh |
| `test_e2e.py` | Kiểm thử mã nguồn, không cần chạy khi mở 5 terminal |

## File đầu ra

Các file được lưu trong `e2e_flow/runs/<tên-lần-chạy>/`.

| File | Nội dung |
|---|---|
| `<run>_<session>.log` | Log dễ đọc riêng cho từng session: hội thoại, tài nguyên và bảng delay |
| `events.jsonl` | Sự kiện gốc và mẫu tài nguyên, mỗi dòng là một bản ghi có timestamp |
| `delays.json` | Delay STT/NLU/TTS và các công đoạn khác, kèm mốc đầu/cuối |
| `summary.json` | Trạng thái từng session/lượt, transcript, phản hồi và các kiểm tra |
| `config.json` | Bản chụp cấu hình của lần chạy |
| `sessions.json` | Bản chụp kịch bản của lần chạy |

Trong `delays.json`, `delay_ms = (end_monotonic_ns - start_monotonic_ns) / 1_000_000`. STT không gồm thời gian thu âm; TTS không gồm thời gian phát loa.
