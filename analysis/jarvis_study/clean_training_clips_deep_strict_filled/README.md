# Deep Clean Training Clips

High-consistency extraction with profile matching to the strongest reference windows.

## Result

- Profile mode: strict
- Total clips: 34
- Total duration: 203.8s (3.40 min)
- Continuity gain clamp: +/-2.00 dB around source baseline
- Continuity block gap threshold: 12.00s
- JARVIS_1: 33 clips across 7 continuity blocks
- JARVIS_II: 1 clips across 1 continuity blocks

## Notes

- Voice-profile consistency scoring applied (pitch, centroid, low-mid, HF/body).
- Boundary refinement and short fades used to reduce edge clicks and partial-word cuts.
- DC removal and gentle 65Hz high-pass used to remove rumble without forcing reverb/noise coloration.
