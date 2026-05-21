#!/bin/bash
# SONNET26 — avvio completo stabile

REGNO="/home/mattia/Scrivania/SONNET26"
VENV="$REGNO/SONNETvenv/bin/python"
DEEPDIR="/mnt/sda3/SONNET26_DATA/DeepSonnet26"
LOG="/tmp/sonnet26_startup.log"

if ! mountpoint -q /mnt/sda3; then
    echo "[!] /mnt/sda3 non montato — output DeepSonnet non disponibile"
fi

echo "[1/5] Pulizia processi precedenti..."
pkill -f "system_monitor/app.py" 2>/dev/null
pkill -f "local_api/app.py" 2>/dev/null
sleep 1

echo "[2/5] DeepSonnet26 (5051) + ollama..."
if ! ss -tlnp | grep -q ':5051'; then
    bash "$DEEPDIR/start.sh" &>> $LOG &
    echo "  → avviato"
else
    echo "  → già online"
fi

echo "[3/5] System monitor (5050)..."
cd "$REGNO" && $VENV system_monitor/app.py &>> $LOG &
sleep 2

echo "[4/5] Local API (5052)..."
cd "$REGNO/local_api" && $VENV app.py &>> $LOG &

echo "[5/5] Attendo servizi..."
for i in {1..15}; do
    S50=$(ss -tlnp | grep -c ':5050')
    S52=$(ss -tlnp | grep -c ':5052')
    [ "$S50" -gt 0 ] && [ "$S52" -gt 0 ] && break
    sleep 1
done

if curl -s http://localhost:5052/api/status | grep -q "online"; then
    echo ""
    echo "✓ Tutto online"

    # [6/6] Sessione giornaliera dual (Claude + DeepSonnet26)
    SESSION_KEY="claudecode_$(date +%Y%m%d)"
    SESSION_LABEL="Claude Code — $(date '+%d %B %Y')"
    BRIEF="Sessione avvio $(date '+%d/%m/%Y %H:%M'). Sistema: Ryzen7 1700, RTX3060 12GB, Ubuntu 24.04."
    curl -s -X POST http://localhost:5052/api/session/start_dual \
        -H "Content-Type: application/json" \
        -d "{\"label\":\"$SESSION_LABEL\",\"key\":\"$SESSION_KEY\",\"brief\":\"$BRIEF\"}" \
        >> $LOG
    echo "  → sessione $SESSION_KEY avviata"

    echo "  → apertura regno..."
    google-chrome --app="http://localhost:5052/" &>/dev/null &
else
    echo "✗ API 5052 non risponde — vedi $LOG"
    exit 1
fi
