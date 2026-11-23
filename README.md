# Workforce Atlas

Jobs, pay, hiring and turnover for every U.S. state and county, from the Census Bureau's Quarterly Workforce Indicators, with unemployment (BLS), population, income and median earnings (Census ACS), cost of living (BEA) and commuting (LEHD).

The site is `index.html`. The data files (`data.js`, `context.js`, `counties/`) are refreshed automatically each month by the GitHub Action in `.github/workflows/refresh-data.yml`, which runs `fetch_qwi.py` and `fetch_context.py`.

API keys are stored as repository secrets (`CENSUS_API_KEY`, `BEA_API_KEY`, `BLS_API_KEY`) and are never committed.
