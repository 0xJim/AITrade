# G60B_SF BTC EMA Band Sweep

- Generated: 2026-06-04T15:58:28
- Baseline reproduced official G60B: yes
- G60B baseline: trades=363, WR=63.6%, PnL=601.27U, DD=3.31%, PF=1.72, ROI/DD=18.14
- Mode: reject `closed_15m_spike`/`spike` long only when BTC 4h EMA9 < EMA21 × (1 - band).

## Sweep Results

| band | trades | WR | PnL | ΔPnL | DD | ΔDD | PF | ROI/DD | removed | removed PnL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 271 | 67.2% | 571.78 | -29.49 | 3.65% | 0.34 | 1.98 | 15.67 | 109 | -39.4 |
| 0.0025 | 310 | 65.8% | 556.91 | -44.36 | 3.22% | -0.09 | 1.8 | 17.3 | 66 | 3.34 |
| 0.005 | 324 | 65.1% | 583.33 | -17.94 | 3.53% | 0.22 | 1.81 | 16.52 | 46 | 1.79 |
| 0.0075 | 339 | 64.6% | 606.68 | 5.41 | 2.94% | -0.37 | 1.8 | 20.66 | 28 | -8.73 |
| 0.01 | 354 | 64.4% | 609.92 | 8.65 | 3.29% | -0.02 | 1.77 | 18.55 | 12 | -16.05 |
| 0.0125 | 356 | 64.0% | 591.41 | -9.86 | 3.29% | -0.02 | 1.73 | 17.95 | 9 | 9.45 |
| 0.015 | 360 | 63.6% | 592.81 | -8.46 | 3.34% | 0.03 | 1.72 | 17.77 | 5 | 12.14 |
| 0.02 | 362 | 63.8% | 605.51 | 4.24 | 3.3% | -0.01 | 1.73 | 18.33 | 1 | -6.56 |
| 0.03 | 363 | 63.6% | 601.27 | 0.0 | 3.31% | 0.0 | 1.72 | 18.14 | 0 | 0 |

## Best historical setting

Best by ROI/DD: band=0.0075 -> PnL=606.68U, DD=2.94%, PF=1.8, ROI/DD=20.66.

## Interpretation

- band=0.005 (the initial SF setting) improves WR/PF but lowers PnL and ROI/DD versus G60B.
- band=0.0075 is the only tested setting that improves PnL, DD, PF, and ROI/DD together while removing net-negative trades.
- The edge is small and based on 28 removed trades, so treat it as a testnet/shadow candidate, not a guaranteed live upgrade.