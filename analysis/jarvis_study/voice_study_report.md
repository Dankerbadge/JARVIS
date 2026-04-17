# JARVIS Voice Study

This report analyzes recording quality and voice characteristics to support clean, artifact-aware voice model preparation.

## JARVIS_1.wav

- Duration: 480.70 s
- Format: 48000 Hz, 2 ch
- Peak: -0.00 dBFS
- RMS: -19.50 dBFS
- Crest Factor: 19.50 dB
- Clipping: 0.0000%
- Silence (< -45 dBFS): 3.49%
- Dynamic Span (P90-P10): 20.23 dB
- Estimated Pitch Median: 151.2 Hz
- Spectral Centroid: 2282.0 Hz
- 85% Roll-off: 5015.6 Hz
- Stereo L/R Correlation: 0.8308
- Stereo L/R Level Delta: 0.05 dB
- Pause Counts: short=15, medium=0, long=0
- Best 6s Window: 0.0s-6.0s (score=100.0, hf/body=0.26)
- Worst 6s Window: 474.0s-480.0s (score=85.1, hf/body=0.67)

## JARVIS_II.wav

- Duration: 16.87 s
- Format: 48000 Hz, 2 ch
- Peak: -5.10 dBFS
- RMS: -26.69 dBFS
- Crest Factor: 21.59 dB
- Clipping: 0.0000%
- Silence (< -45 dBFS): 4.16%
- Dynamic Span (P90-P10): 17.30 dB
- Estimated Pitch Median: 179.1 Hz
- Spectral Centroid: 4431.9 Hz
- 85% Roll-off: 10406.2 Hz
- Stereo L/R Correlation: 0.9133
- Stereo L/R Level Delta: -0.02 dB
- Pause Counts: short=2, medium=0, long=0
- Best 6s Window: 6.0s-12.0s (score=100.0, hf/body=0.32)
- Worst 6s Window: 0.0s-6.0s (score=-115.9, hf/body=2.18)

## Cross-File Consistency

- Pitch median delta: 27.9 Hz
- RMS delta: 7.19 dB
- Spectral centroid delta: 2149.9 Hz

## Recommended Preprocessing Targets

- Keep source at 48 kHz while editing; export training-ready WAV as mono 48 kHz, 16-bit PCM.
- Remove only non-stationary background noise; avoid aggressive denoise that smears consonants.
- Use light de-reverb only if room tail is obvious in pauses; prioritize preserving natural timbre.
- Loudness-normalize segments to a consistent speech RMS window before model ingestion.
- Exclude long pauses and heavily noisy segments from training clips.
