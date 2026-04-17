# Clean Training Clips

Auto-selected and normalized windows for voice model training.

## Rules

- Window: 6.00s, hop: 3.00s, minimum gap: 0.12s.
- Composite quality score based on loudness, silence, brightness (HF/body), and low-mid body.
- Adaptive threshold relaxation across up to 4 passes to meet per-source targets without over-admitting low quality windows.
- Normalization: target RMS -23.0 dBFS, peak ceiling -1.0 dBFS.
- Fade in/out: 12.0 ms to avoid clip-boundary clicks.
- Export format: mono WAV, 48kHz, 16-bit PCM.

## Result

- Total clips: 46
- Total duration: 276.0s (4.60 min)
- JARVIS_1: 45 clips
- JARVIS_II: 1 clips
- Selection pass usage:
  - pass 1: 44 clips
  - pass 3: 1 clips
  - pass 4: 1 clips
