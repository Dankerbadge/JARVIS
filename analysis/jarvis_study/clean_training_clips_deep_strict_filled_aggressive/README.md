# Deep Clean Training Clips

High-consistency extraction with profile matching to the strongest reference windows.

## Result

- Profile mode: strict
- Total clips: 45
- Total duration: 269.1s (4.48 min)
- Continuity gain clamp: +/-2.00 dB around source baseline
- Continuity block gap threshold: 12.00s
- JARVIS_1: 44 clips across 1 continuity blocks
- JARVIS_II: 1 clips across 1 continuity blocks

## Notes

- Voice-profile consistency scoring applied (pitch, centroid, low-mid, HF/body).
- Boundary refinement and short fades used to reduce edge clicks and partial-word cuts.
- DC removal and gentle 65Hz high-pass used to remove rumble without forcing reverb/noise coloration.
