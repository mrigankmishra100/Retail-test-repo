# Supplier Policy PDFs

This directory contains the four original single-page synthetic POC policy PDFs:

- `SUP001_policy.pdf`
- `SUP002_policy.pdf`
- `SUP003_policy.pdf`
- `SUP004_policy.pdf`

The documents are unchanged. `tests/test_source_release.py` verifies the sample
supplier rows against their prices, minimum quantities and standard lead times.
Do not replace them with empty placeholders or use them as real supplier contracts.

The complete one-page document will be read directly without chunking, embeddings, RAG, or a vector database. The PDFs will later be uploaded to OCI Object Storage with `scripts/upload_policies.py`.
