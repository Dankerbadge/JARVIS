# Fine-Tuned Hybrid Pack

Regression-safe fine-tuning pass on the hybrid clarity-strict pack.

## Result

- Input clips: 33
- Tuned selected: 25
- Original retained: 8
- Mean raw delta total score (tuned - original): +0.539
- Median raw delta total score (tuned - original): +0.868
- Mean final total score gain vs original baseline: +0.843
- Mean final clarity gain vs original baseline: +0.117

## Continuity

- Gain smoothing is applied per source in timeline order before final A/B selection.
- Per-clip hard guards block regressions in harmonicity, flatness, hiss, silence, and peak/clipping risk.

## Files

- `finetune_selection.csv`
- `finetune_playlist.m3u`
