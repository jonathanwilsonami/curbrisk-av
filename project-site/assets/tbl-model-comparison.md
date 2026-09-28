| window          | model         | n | k | llf    | AIC   | BIC   | pearson_chi2_df | KS_D   | KS_p   |
| --------------- | ------------- | - | - | ------ | ----- | ----- | --------------- | ------ | ------ |
| Full (n=8)      | Poisson       | 8 | 2 | -37.12 | 78.23 | 78.39 | 5.15            | 0.2378 | 0.6723 |
| Full (n=8)      | Neg. binomial | 8 | 3 | -32.92 | 71.85 | 72.09 | 5.03            | 0.1395 | 0.9908 |
| Post-ramp (n=6) | Poisson       | 6 | 2 | -17.88 | 39.76 | 39.34 | 0.29            | 0.2901 | 0.5979 |
| Post-ramp (n=6) | Neg. binomial | 6 | 3 | -17.88 | 41.76 | 41.14 | 0.29            | 0.2901 | 0.5978 |

: Poisson vs. Negative-Binomial model comparison (log-likelihood, AIC, BIC, Pearson dispersion, discrete GOF KS test), full vs. post-ramp window. {#tbl-model-comparison}
