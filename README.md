# ⬡ SONNET26

**Regno operativo AI** — ambiente di sviluppo collaborativo tra Mattia e Claude Sonnet 4.6.

## Struttura locale
```
~/Scrivania/SONNET26/          ← regno Claude (NVMe)
├── dashboard.html             ← log viewer
├── memory.md                  ← memoria persistente
├── log.jsonl                  ← operazioni append-only
├── system_monitor/            ← Flask :5050 (CPU/GPU/RAM live)
├── SONNETvenv/                ← Python venv
└── data/                      ← symlink → /mnt/sda3/SONNET26_DATA

/mnt/sda3/SONNET26_DATA/       ← storage 1TB (Windows NTFS)
└── DeepSonnet26/              ← AI locale Flask :5051
    ├── app.py                 ← API /api/chat /api/status
    ├── templates/index.html   ← interfaccia chat
    └── venv/
```

## Servizi locali
| Servizio | Porta | Stato |
|---|---|---|
| System Monitor | :5050 | Flask + psutil + nvidia-smi |
| DeepSonnet26 | :5051 | deepseek-r1:14b via Ollama |
| Ollama | :11434 | GPU RTX3060 12GB |

## Macchina
- CPU: AMD Ryzen 7 1700 (8C/16T)
- GPU: NVIDIA RTX3060 12GB VRAM
- Storage: 238GB NVMe + 1TB HDD

## Progetti principali (PycharmProjects)
- **X6 Drone Hybrid VTOL** — ArduPilot, MAVLink, CAN bus, AFS v2
- **Adelchi Web Platform** — Flask, ADG token blockchain, mining pool
- **AutoTrade** — trading algoritmico Python
- **FreeCAD Drone** — modelli 3D fusoliera, ali, turbina
- **PALLET Gestionale** — magazzino Flask+SQLite
- **EICO / DDM** — e-commerce Amazon/Shopify HTML

---
*Adelchi Group SRLS · Milano · 2026*
