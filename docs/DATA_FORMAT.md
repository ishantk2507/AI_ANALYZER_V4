# Data format

## Supported files

AI Analyzer discovers `.parquet` and `.csv` files. Parquet is preferred. If a
directory contains both `MAKER.csv` and `MAKER.parquet`, only the Parquet file
is catalogued.

Use either a flat directory:

```text
data/
  MAKER.parquet
  FUEL.csv
```

or category folders:

```text
data/
  Two Wheeler/
    MAKER.parquet
    FUEL.parquet
  Three Wheeler/
    MAKER.parquet
```

The category is the folder name and the breakdown is the filename without its
extension. Hidden folders are ignored. The catalog can combine the same
breakdown across multiple categories for cross-category questions.

## Schema expectations

There is no mandatory column list. The profiler classifies columns dynamically:

- Numeric columns are available as measures; `TOTAL` is preferred when a plan
  does not specify a metric.
- Text, booleans, and categorical columns are dimensions and can be grouped or
  filtered.
- Columns named `YEAR`, `YR`, `MONTH`, `QUARTER`, `DATE`, `PERIOD`, or `FY`
  (case-insensitive), plus datetime columns, are temporal dimensions.

For reliable questions, use stable, descriptive names such as `STATE`, `CITY`,
`MAKER`, `FUEL`, `CLASS`, `YEAR`, and `TOTAL`. The provided Vahan-oriented
glossary recognises `MAKER`, `FUEL`, `CLASS`, and `NORM`, but other names work.

## Data quality guidance

- Store one consistent unit per numeric measure. Do not mix counts, currency,
  and percentages in the same metric column.
- Keep a temporal column when period-over-period growth is required.
- Use clean, stable values for geographic and categorical dimensions.
- Preserve zeroes as numeric zeroes; use empty values for unknowns rather than
  placeholder strings such as `N/A` in a numeric column.
- Do not place duplicate breakdowns in multiple files unless you intend them to
  be analysed separately.

The app reads source files only. Dataset, profile, and model-response caches
can be cleared safely; source data cannot be restored from the application.
