# CAISO Energy Forecasting

Forecasting renewable energy production in California from weather data, then extending the same
machinery to forecast electricity prices (LMPs) on the CAISO grid.

This is a **learning project as much as a modeling project**. It is deliberately structured in
tiers, where each tier adds one new skill on top of a working system from the previous tier. The
goal is to come out the other side knowing how to pull data from a real REST API, shape it in
pandas, persist it in SQL, and train models in PyTorch — not just to produce a number.

---

## Goals

**Modeling goals**

1. Predict hourly solar and wind generation in California from weather conditions.
2. Predict system-level CAISO Locational Marginal Prices (LMPs).
3. Predict nodal LMPs at individual pricing nodes.

**Learning goals** (the real point)

| Skill | Where it gets learned |
|---|---|
| How REST APIs work — endpoints, query params, auth, pagination, rate limits | Tier 0–1 |
| Reading API docs and constructing a URL to get *exactly* the slice you want | Tier 1 |
| Data cleaning and reshaping in pandas — joins, resampling, missing data, time zones | Tier 1–2 |
| Relational data modeling and SQL | Tier 2 |
| Baselines and classical ML (linear regression, trees, forests, XGBoost) | Tier 3 |
| PyTorch — tensors, `Dataset`/`DataLoader`, training loops, autograd, MLPs, sequence models | Tier 4 |
| Honest time-series evaluation — no leakage, walk-forward validation | Tier 3 onward |
| Scaling a pipeline from one target to hundreds (nodal prices) | Tier 6 |

---

## Tier plan

Each tier ends in something that runs. Do not start the next tier until the current one works
end to end.

### Tier 0 — Get one API response

Goal: understand what an API actually is before automating it.

- Register for an [EIA API key](https://www.eia.gov/opendata/register.php).
- Pull a single hour of CAISO generation with `requests`. Print the raw JSON. Look at it.
- Change the URL by hand: change the date, change the fuel type, change `length`, break it on
  purpose and read the error message.
- Understand each piece of the query string: `api_key`, `frequency`, `data[]`, `facets[]`,
  `start`, `end`, `sort[]`, `offset`, `length`.

**Done when:** you can write a URL from scratch that returns solar generation for one specific day
without copying it from anywhere.

### Tier 1 — Automated ingest

Goal: turn manual URL-poking into a repeatable fetcher.

- Wrap the EIA call in a function: `fetch_generation(start, end, fuel_type)`.
- Handle pagination (EIA caps rows per response — loop on `offset` until exhausted).
- Handle failures: retries with backoff, non-200 status codes, empty payloads.
- Keep the API key out of the repo (`.env`, read via `os.environ`).
- Add a weather fetcher against a second API — **Meteostat** — so you see that not all APIs look
  alike (different auth style, different pagination, different date format).
- Cache raw responses to disk as JSON/Parquet so you never re-download the same window.

**Done when:** one command pulls multiple years of hourly generation and weather and writes it to
local files.

### Tier 2 — Clean it and store it in SQL

Goal: a queryable, trustworthy dataset. This tier is where most of the real work is.

- Normalize timestamps to a single convention (UTC in storage, `America/Los_Angeles` for
  interpretation). Handle the DST spring-forward gap and fall-back duplicate hour explicitly.
- Resample everything to a common hourly grid.
- Audit missing data: how many gaps, how long, are they random or systematic? Decide per-column
  whether to interpolate, forward-fill, or leave null — and write down why.
- Find and handle outliers (negative solar, sensor spikes, curtailment artifacts).
- Design the schema and load it with SQLAlchemy. Starting shape:

  ```
  weather_stations   (station_id PK, name, lat, lon, elevation)
  weather_hourly     (station_id FK, ts_utc, temp_c, ghi, dni, cloud_cover,
                      wind_speed_10m, wind_speed_100m, wind_dir, pressure, humidity)
                     PRIMARY KEY (station_id, ts_utc)
  generation_hourly  (ba_code, ts_utc, fuel_type, mwh)
                     PRIMARY KEY (ba_code, ts_utc, fuel_type)
  demand_hourly      (ba_code, ts_utc, demand_mwh, net_demand_mwh)
  lmp_system_hourly  (ts_utc, market_run, lmp, energy, congestion, loss)   -- Tier 5
  nodes              (node_id PK, node_name, lat, lon, zone)               -- Tier 6
  lmp_nodal_hourly   (node_id FK, ts_utc, market_run, lmp, congestion, loss)
                     PRIMARY KEY (node_id, ts_utc, market_run)
  ```

- Add indexes on `ts_utc` and write a few analytical queries in raw SQL (monthly solar totals,
  hours where wind exceeded X, join weather to generation on timestamp).

**Done when:** a single SQL query returns a clean, gap-free hourly feature table ready for modeling.

### Tier 3 — Baselines and classical ML

Goal: know what "good" means before reaching for a neural network.

- Build naive baselines first: persistence (`y_t = y_{t-24}`), seasonal climatology mean.
  **Every later model must beat these or it is not working.**
- Feature engineering: hour-of-day and day-of-year as sin/cos pairs, lags (t-1, t-24, t-168),
  rolling means, solar zenith angle, `wind_speed ** 3` (power scales with the cube).
- Split by **time**, never randomly — e.g. train 2019–2022, validate 2023, test 2024. Random
  splits leak the future into the past and will make everything look great and be worthless.
- Models, in increasing complexity: linear regression → ridge/lasso → decision tree →
  random forest → gradient boosting → XGBoost.
- Metrics: MAE, RMSE, MAPE (careful — solar is zero at night, so MAPE explodes), skill score
  vs. the persistence baseline.
- Inspect feature importances and SHAP values. Do they match physical intuition? If irradiance
  is not the top solar feature, something is wrong with the data, not the model.

**Done when:** XGBoost meaningfully beats persistence on a held-out year, and you can explain why.

### Tier 4 — PyTorch

Goal: learn the framework on a problem you already have a strong baseline for.

- Re-implement linear regression in PyTorch by hand — tensors, `nn.Linear`, MSE loss, an
  optimizer, and a training loop written out explicitly. Confirm it matches scikit-learn's
  coefficients. This is the single most useful exercise in the project.
- Write a `Dataset` and `DataLoader`. Understand batching, shuffling (and why you don't shuffle
  across a time boundary for sequence models), and `num_workers`.
- Scale up to an MLP: hidden layers, ReLU, dropout, batch norm. Add early stopping on validation
  loss and learning-rate scheduling.
- Then sequence models, since generation is a time series: LSTM/GRU on a 48-hour input window,
  and a 1D CNN or small temporal transformer for comparison.
- Move to multi-horizon forecasting: predict t+1 … t+24 in one shot rather than one step ahead.
- Log every run (loss curves, hyperparameters, metrics) so results are comparable.

**Done when:** a PyTorch model is competitive with XGBoost, and you understand each line of the
training loop.

### Tier 5 — System-level LMP forecasting

Goal: switch targets from physics to economics, and see what breaks.

- New data source: CAISO OASIS for day-ahead and real-time prices (see below).
- Predict system/hub-level LMP. Features: forecast load, forecast solar and wind (your Tier 3–4
  outputs feed in here), net load, imports, gas prices, hour, day type.
- Expect this to be much harder than generation. Prices are heavy-tailed, occasionally negative
  (oversupply and curtailment), and occasionally spike by orders of magnitude.
- Because of that, try: log or signed-log target transforms, quantile regression (predict p10 /
  p50 / p90 instead of a point), and classification for the tails ("will price go negative?",
  "will price exceed $200/MWh?").
- Evaluate on the tails specifically, not just average error. A model with great MAE that misses
  every spike is useless for the actual decisions prices drive.

**Done when:** you can produce a calibrated day-ahead price distribution, not just a point estimate.

### Tier 6 — Nodal LMP forecasting

Goal: scale from one target to thousands, and confront congestion.

- Nodal LMP decomposes as **energy + congestion + loss**. The energy component is ~system-wide;
  the interesting, hard, node-specific part is congestion. Model the components separately.
- Start with a handful of nodes (one urban load node, one solar-heavy node in the Central Valley,
  one wind node near Tehachapi), then expand.
- Compare strategies: one model per node vs. a single multi-output model vs. a shared embedding
  where node identity is a learned vector.
- Add spatial structure: node latitude/longitude, nearest weather station, and eventually a graph
  neural network over the network topology if you want to push it.
- Watch the practical problems: thousands of targets, sparse and shifting node sets, storage size,
  training time.

**Done when:** nodal forecasts beat "system LMP applied uniformly to every node."

---

## Data sources

| Source | Provides | Notes |
|---|---|---|
| [EIA Open Data v2](https://www.eia.gov/opendata/) | Hourly CAISO generation by fuel type, demand, interchange (EIA-930) | Free API key. Primary source for Tiers 1–4. Hourly granularity, ~1–2 day lag. |
| [Meteostat](https://meteostat.net/) | Temperature, wind speed/direction, pressure, humidity, cloud cover, precipitation | **The EIA API does not serve weather** — this is a separate fetcher. **Weather data comes from Meteostat**, accessed via its free Python library / JSON API (no key required) with an hourly historical archive. Note: Meteostat does not provide solar irradiance (GHI/DNI) directly, so derive or proxy it if the solar model needs it. |
| [CAISO OASIS](http://oasis.caiso.com/mrioasis/logon.do) | Day-ahead and real-time LMPs, system and nodal; load forecasts; curtailment | **The EIA API does not serve LMPs.** OASIS is a SOAP-ish XML/ZIP interface, not clean JSON — good, if painful, API practice for Tier 5. [GridStatus](https://www.gridstatus.io/) is a friendlier wrapper if OASIS becomes a blocker. |
| [CAISO Today's Outlook](https://www.caiso.com/todays-outlook) | Near-real-time renewables and curtailment | Useful for sanity checks. |

Both extra sources are additive — nothing about Tiers 0–4 changes because of them. Just be aware
early that "fetch everything from EIA" won't cover weather or prices.

---

## Planned layout

```
CAISO-Energy-Forecasting/
├── data/
│   ├── raw/              # untouched API responses (gitignored)
│   ├── interim/          # partially cleaned
│   └── caiso.db          # SQLite (gitignored)
├── notebooks/            # exploration only — nothing important lives here
├── src/
│   ├── ingest/
│   │   ├── eia.py        # EIA-930 generation & demand
│   │   ├── weather.py    # irradiance & wind
│   │   └── caiso_lmp.py  # OASIS prices (Tier 5+)
│   ├── db/
│   │   ├── schema.py     # SQLAlchemy models
│   │   └── load.py       # upserts
│   ├── features/
│   │   └── build.py      # SQL -> model-ready DataFrame
│   ├── models/
│   │   ├── baselines.py  # persistence, climatology
│   │   ├── sklearn_models.py
│   │   ├── xgb.py
│   │   └── torch/        # datasets, MLP, LSTM, training loop
│   └── eval/
│       └── metrics.py
├── configs/              # YAML per experiment
├── tests/
└── README.md
```

---

## Setup

```bash
git clone <this-repo>
cd CAISO-Energy-Forecasting

python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` in the project root (already gitignored):

```
EIA_API_KEY=your_key_here
```

**Core dependencies:** `requests`, `pandas`, `numpy`, `sqlalchemy`, `python-dotenv`,
`scikit-learn`, `xgboost`, `torch`, `matplotlib`, `pyarrow`.

---

## Things that will bite you

Written down in advance so they cost hours instead of days.

- **Time zones.** CAISO operates in Pacific time; EIA returns UTC-ish timestamps with an hour
  offset field. Store UTC, convert once, and never mix. DST creates one missing hour in spring
  and one duplicated hour in fall — decide how to handle both before loading anything.
- **Random train/test splits.** On time series they leak the future into training. Split by date.
  If a model looks suspiciously good, this is the first thing to check.
- **Fitting the scaler on all the data.** Fit on train only, then transform validation and test.
  Same for imputation values and target transforms.
- **MAPE on solar.** Nighttime generation is zero, so percentage error is undefined or infinite.
  Use MAE/RMSE, or restrict MAPE to daylight hours.
- **Not beating persistence.** A 24-hour-lag copy is a strong baseline. If a deep model doesn't
  beat it, the model isn't learning anything real.
- **Negative prices are real.** Don't clip them; they're the most interesting hours in a
  renewables-heavy grid.
- **API pagination silently truncating.** EIA returns a capped number of rows. If you don't loop
  on `offset`, you'll quietly train on a fraction of the data you think you have.
- **Curtailment.** Low solar output on a sunny day may mean the grid rejected the power, not that
  the weather was bad. Weather features cannot explain that, and it puts a ceiling on achievable
  accuracy.

---

## Progress

- [ ] Tier 0 — first successful EIA API call
- [ ] Tier 1 — automated, paginated, cached ingest (generation + weather)
- [ ] Tier 2 — cleaned data loaded into SQL with a working feature query
- [ ] Tier 3 — baselines + XGBoost beating persistence on a held-out year
- [ ] Tier 4 — PyTorch MLP and LSTM competitive with XGBoost
- [ ] Tier 5 — system-level LMP forecasts with quantiles
- [ ] Tier 6 — nodal LMP forecasts beating a uniform-system-price baseline

---

## License

MIT — see [LICENSE](LICENSE).
