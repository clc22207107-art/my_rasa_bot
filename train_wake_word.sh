#!/bin/bash
# ============================================================
# Train custom wake word model với openWakeWord
# Chạy trong venv riêng (wake_word_venv) để không ảnh hưởng Rasa
#
# Cách dùng:
#   bash train_wake_word.sh "ok vendy"
#   bash train_wake_word.sh "hey machine"
#   bash train_wake_word.sh "hi vendor"
# ============================================================

PHRASE="${1:-ok vendy}"
OUTPUT_DIR="$(dirname "$0")/wake_models"
VENV="$HOME/wake_word_venv"

echo "================================================="
echo "  Wake word training: \"$PHRASE\""
echo "  Output: $OUTPUT_DIR"
echo "================================================="

# Kích hoạt venv riêng
source "$VENV/bin/activate"

mkdir -p "$OUTPUT_DIR"

# Train — openWakeWord tự tổng hợp audio từ TTS rồi train
python -m openwakeword.train \
  --training_phrase "$PHRASE" \
  --output_dir "$OUTPUT_DIR" \
  --n_epochs 30 \
  --target_false_positive_rate 0.5

echo ""
echo "================================================="
echo "  Xong! Model lưu tại: $OUTPUT_DIR"
ls "$OUTPUT_DIR"/*.onnx 2>/dev/null || ls "$OUTPUT_DIR"/*.tflite 2>/dev/null
echo ""
echo "  Để dùng trong speech_input.py, thay:"
SAFE=$(echo "$PHRASE" | tr ' ' '_')
echo "    WAKE_WORD_MODELS = [\"$OUTPUT_DIR/${SAFE}.onnx\"]"
echo "================================================="
