# Supplier Policy PDFs

This directory will contain one single-page policy PDF for each POC supplier:

- `SUP001_policy.pdf`
- `SUP002_policy.pdf`
- `SUP003_policy.pdf`
- `SUP004_policy.pdf`

Do not add empty placeholder PDFs. Each final document will contain the supplier ID, supplier name, supported products, minimum order quantity, standard price, bulk discount slabs, standard delivery time, expedited delivery conditions, and payment terms.

The complete one-page document will be read directly without chunking, embeddings, RAG, or a vector database. The PDFs will later be uploaded to OCI Object Storage with `scripts/upload_policies.py`.
