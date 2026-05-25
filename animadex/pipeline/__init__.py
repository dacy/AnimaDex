"""The data pipeline: ingest CSV rows, generate/build images, score
artists, sync LoRAs. Each step is independently callable so the
orchestrator can run them per row, and so a user can invoke a single
step from the CLI when that's all they need."""
